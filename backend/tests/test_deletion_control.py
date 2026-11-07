"""Independent journal/restore and backup completion evidence checks."""
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from uuid import uuid4
import pytest
from test_cleanup import cleanup_db,queued,job,asset


def test_journal_contains_only_control_metadata_and_keeps_first_intent(cleanup_db):
    from app import cleanup,deletion_control
    db=cleanup_db;uid,tid=queued(db)
    first=datetime.now(timezone.utc)-timedelta(seconds=2)
    deletion_control.record_intent(tid,uid,first)
    cleanup.request_delete(tid,uid)
    root=deletion_control._root();raw=json.loads((root/(str(tid)+'.json')).read_text())
    assert set(raw)=={'task_id','user_id','requested_at'}
    assert datetime.fromisoformat(raw['requested_at'])==first
    assert deletion_control.blocked(tid)


def test_replay_restores_fence_when_database_snapshot_lost_delete(cleanup_db):
    from app import cleanup,deletion_control,tasks
    db=cleanup_db;uid,tid=queued(db)
    cleanup.request_delete(tid,uid)
    # Model a restored snapshot retaining a cleanup receipt but missing the fence.
    db.execute("UPDATE tasks SET deletion_requested_at=NULL,status='QUEUED' WHERE id=%s",(tid,))
    assert deletion_control.blocked(tid)
    deletion_control.replay_journal()
    assert db.execute('SELECT deletion_requested_at FROM tasks WHERE id=%s',(tid,)).fetchone()[0]
    assert tasks.list_tasks(uid)==[]


def test_restore_force_rechecks_deleted_assets_without_publishing_task(cleanup_db):
    from app import cleanup,deletion_control
    db=cleanup_db;uid,tid=queued(db)
    aid=asset(db,uid,tid,'ORIGINAL','originals/'+uuid4().hex)
    cleanup.request_delete(tid,uid)
    db.execute("UPDATE media_assets SET cleanup_state='DELETED',deleted_at=now() WHERE id=%s",(aid,))
    db.execute("UPDATE cleanup_jobs SET job_version=job_version+1,status='COMPLETED',online_completed_at=now(),completed_at=now() WHERE task_id=%s",(tid,))
    db.execute('UPDATE tasks SET deleted_at=now() WHERE id=%s',(tid,))
    assert deletion_control.replay_journal(force=True)==[str(tid)]
    assert db.execute('SELECT cleanup_state FROM media_assets WHERE id=%s',(aid,)).fetchone()[0]=='REGISTERED'
    assert job(db,tid)[1]=='PENDING'
    assert db.execute('SELECT deletion_requested_at,deleted_at FROM tasks WHERE id=%s',(tid,)).fetchone()[0]


def test_missing_or_wrong_external_identity_fails_closed(cleanup_db):
    from app import deletion_control
    path=deletion_control._root()/'identity.json'
    original=path.read_text();path.unlink()
    with pytest.raises(deletion_control.JournalError):deletion_control.initialize()
    path.write_text(json.dumps({'deployment_id':str(uuid4())}))
    with pytest.raises(deletion_control.JournalError):deletion_control.initialize()
    path.write_text(original)


def test_wrong_task_owner_in_restored_snapshot_is_rejected(cleanup_db):
    from app import cleanup,deletion_control
    db=cleanup_db;uid,tid=queued(db);cleanup.request_delete(tid,uid)
    path=deletion_control._root()/(str(tid)+'.json');entry=json.loads(path.read_text());entry['user_id']=str(uuid4());path.write_text(json.dumps(entry))
    with pytest.raises(deletion_control.JournalError):deletion_control.replay_journal()


def test_corrupt_journal_does_not_silently_skip_control(cleanup_db):
    from app import deletion_control
    path=deletion_control._root()/(str(uuid4())+'.json');path.write_text('{invalid')
    with pytest.raises(deletion_control.JournalError):deletion_control.replay_journal()


def test_unknown_backup_inventory_stays_pending(cleanup_db,monkeypatch):
    from app import cleanup,deletion_control
    db=cleanup_db;uid,tid=queued(db);cleanup.request_delete(tid,uid)
    asset(db,uid,tid,'BACKUP','backups/'+uuid4().hex)
    monkeypatch.delenv('BACKUP_INVENTORY_CONFIRMED',raising=False)
    assert deletion_control.cleanup_backups(tid)=='PENDING'


def test_backup_object_still_present_never_marked_complete(cleanup_db,monkeypatch):
    from app import cleanup,deletion_control,object_store
    db=cleanup_db;uid,tid=queued(db);cleanup.request_delete(tid,uid)
    aid=asset(db,uid,tid,'BACKUP','backups/'+uuid4().hex)
    class Storage:
        def list_multipart_uploads(self,**kwargs):return {'Uploads':[]}
        def head_bucket(self,**kwargs):pass
        def get_bucket_versioning(self,**kwargs):return {}
        def delete_object(self,**kwargs):pass
        def head_object(self,**kwargs):return {'ContentLength':1}
    monkeypatch.setattr(object_store,'client',lambda:Storage())
    monkeypatch.setattr(object_store,'cleanup_client',lambda:Storage())
    db.execute('UPDATE media_assets SET cleanup_approved_at=now() WHERE id=%s',(aid,))
    monkeypatch.setattr(object_store,'bucket',lambda:'synthetic-private')
    monkeypatch.setenv('BACKUP_INVENTORY_CONFIRMED','true')
    assert deletion_control.cleanup_backups(tid)=='PENDING'
    assert db.execute('SELECT cleanup_state FROM media_assets WHERE id=%s',(aid,)).fetchone()[0]!='DELETED'
    assert job(db,tid)[1]=='RETRY_WAIT'


def test_unverified_backup_at_thirty_days_raises_persistent_alert(cleanup_db):
    from app import cleanup
    db=cleanup_db;uid,tid=queued(db);cleanup.request_delete(tid,uid)
    asset(db,uid,tid,'BACKUP','backups/'+uuid4().hex)
    db.execute("UPDATE cleanup_jobs SET job_version=job_version+1,started_at=now()-interval '31 days' WHERE task_id=%s",(tid,))
    cleanup.run_once()
    assert db.execute("SELECT count(*) FROM cleanup_alerts WHERE task_id=%s AND alert_code='LEGACY_APPROVAL' AND resolved_at IS NULL",(tid,)).fetchone()[0]==1
