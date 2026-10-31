"""Independent S3-09 deletion fences, asset cleanup, retention and operator checks."""
import asyncio
from datetime import datetime,timedelta,timezone
import os
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict,make_conninfo
import pytest
from fastapi import HTTPException
from test_results import candidate,identity,claimed,publish,client,authenticated

@pytest.fixture
def cleanup_db(monkeypatch,tmp_path):
    base=os.getenv('TEST_DATABASE_URL')
    if not base:pytest.skip('Explicit isolated *_test database required')
    if not conninfo_to_dict(base).get('dbname','').endswith('_test'):pytest.fail('Only *_test database allowed')
    schema='cleanup_test_'+uuid4().hex
    with psycopg.connect(base,autocommit=True) as control:
        control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        monkeypatch.setenv('DATABASE_URL',make_conninfo(base,options='-csearch_path='+schema))
        monkeypatch.setenv('QUEUE_GLOBAL_CONCURRENCY','4')
        monkeypatch.setenv('WORKER_LEASE_SECONDS','120')
        monkeypatch.setenv('WORKER_HEARTBEAT_SECONDS','30')
        monkeypatch.setenv('PROCESSING_WORK_DIR',str(tmp_path/'processing'))
        monkeypatch.setenv('DELETION_JOURNAL_DIR',str(tmp_path/'journal'))
        try:
            from app.migrate import run_migrations
            with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as db:
                run_migrations(db)
                from app.deletion_control import initialize
                initialize()
                yield db
        finally:control.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

def queued(db,uid=None,state='QUEUED',age=0):
    uid=uid or identity(db)
    tid=db.execute("INSERT INTO tasks(user_id,input_kind,status,display_filename,duration_seconds,created_at) VALUES (%s,'LOCAL',%s,'synthetic.mp4',60,now()-%s*interval '1 day') RETURNING id",(uid,state,age)).fetchone()[0]
    return uid,tid

def job(db,tid):return db.execute('SELECT id,status,attempt_count FROM cleanup_jobs WHERE task_id=%s',(tid,)).fetchone()

def asset(db,uid,tid,kind,key):
    return db.execute('INSERT INTO media_assets(user_id,task_id,asset_kind,storage_key) VALUES (%s,%s,%s,%s) RETURNING id',(uid,tid,kind,key)).fetchone()[0]

def test_delete_queued_is_immediate_idempotent_and_not_charged(cleanup_db):
    from app import cleanup,tasks,queue
    db=cleanup_db;uid,tid=queued(db)
    cleanup.request_delete(tid,uid);cleanup.request_delete(tid,uid)
    row=db.execute('SELECT status,deletion_requested_at FROM tasks WHERE id=%s',(tid,)).fetchone()
    assert row[0]=='CANCELLED' and row[1]
    assert tasks.list_tasks(uid)==[] and queue.claim_next('cannot-run') is None
    assert db.execute('SELECT count(*) FROM cleanup_jobs WHERE task_id=%s',(tid,)).fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM quota_events WHERE task_id=%s',(tid,)).fetchone()[0]==0
    with pytest.raises(HTTPException):tasks.get_task(tid,uid)
    with pytest.raises(HTTPException):tasks.request_retry(tid,uid,uuid4())

def test_active_delete_fences_old_worker_without_refund(cleanup_db):
    from app import cleanup,tasks,queue
    db=cleanup_db;uid,tid,generation=claimed(db)
    cleanup.request_delete(tid,uid)
    assert db.execute('SELECT credits_available FROM users WHERE id=%s',(uid,)).fetchone()[0]==2
    assert not queue.heartbeat(tid,generation,'result-test-worker')
    with pytest.raises(HTTPException):publish(tid,generation)
    assert db.execute('SELECT count(*) FROM task_results WHERE task_id=%s',(tid,)).fetchone()[0]==0


def test_tenant_cannot_delete_other_users_task(cleanup_db):
    from app import cleanup
    db=cleanup_db;uid,tid=queued(db);outsider=identity(db)
    with pytest.raises(HTTPException):cleanup.request_delete(tid,outsider)
    assert db.execute('SELECT deletion_requested_at FROM tasks WHERE id=%s',(tid,)).fetchone()[0] is None


def test_completed_bilingual_result_bodies_and_assets_clear(cleanup_db):
    from app import cleanup
    db=cleanup_db;uid,tid,generation=claimed(db);publish(tid,generation)
    cleanup.request_delete(tid,uid)
    cleanup.process_job(job(db,tid)[0])
    assert db.execute('SELECT count(*) FROM task_results WHERE task_id=%s',(tid,)).fetchone()[0]==0
    assert db.execute("SELECT count(*) FROM media_assets WHERE task_id=%s AND cleanup_state<>'DELETED'",(tid,)).fetchone()[0]==0
    receipt=cleanup.list_jobs(uid)[0]
    assert receipt['online_complete'] is True
    assert receipt['backup_cleanup_state']!='COMPLETED' # no evidence for unregistered/manual backups

@pytest.mark.parametrize('kind',['EVALUATION_COPY','ANNOTATION','MODEL_OUTPUT','FAILURE_CASE'])
def test_linked_evaluation_artifact_is_not_exempt(cleanup_db,monkeypatch,kind):
    from app import cleanup,object_store
    from botocore.exceptions import ClientError
    db=cleanup_db;uid,tid=queued(db);key='evaluation/'+uuid4().hex
    aid=asset(db,uid,tid,kind,key)
    db.execute('INSERT INTO evaluation_links(task_id,user_id,asset_id,link_kind) VALUES (%s,%s,%s,%s)',(tid,uid,aid,kind))
    deleted=[]
    class Storage:
        def delete_object(self,**kw):deleted.append(kw['Key'])
        def head_object(self,**kw):raise ClientError({'Error':{'Code':'404'}},'HeadObject')
    monkeypatch.setattr(object_store,'client',lambda:Storage())
    monkeypatch.setattr(object_store,'bucket',lambda:'synthetic-private')
    cleanup.request_delete(tid,uid);cleanup.process_job(job(db,tid)[0])
    assert deleted==[key]
    row=db.execute('SELECT cleanup_state FROM media_assets WHERE id=%s',(aid,)).fetchone()
    assert row is None or row[0]=='DELETED'


def test_unknown_asset_cannot_be_reported_deleted(cleanup_db):
    from app import cleanup
    db=cleanup_db;uid,tid=queued(db)
    aid=asset(db,uid,tid,'PROCESSING_WORKSPACE','../../unowned')
    cleanup.request_delete(tid,uid);cleanup.process_job(job(db,tid)[0])
    assert db.execute('SELECT cleanup_state FROM media_assets WHERE id=%s',(aid,)).fetchone()[0]!='DELETED'
    assert cleanup.list_jobs(uid)[0]['online_complete'] is False

@pytest.mark.parametrize('state',['QUEUED','PROCESSING','SUCCEEDED','FAILED','CANCELLED'])
def test_thirty_day_expiry_covers_all_states(cleanup_db,state):
    from app import cleanup
    db=cleanup_db;uid,tid=queued(db,state=state,age=31)
    _,fresh=queued(db,uid,age=29)
    cleanup.run_once()
    assert db.execute('SELECT deletion_requested_at FROM tasks WHERE id=%s',(tid,)).fetchone()[0]
    assert db.execute('SELECT deletion_requested_at FROM tasks WHERE id=%s',(fresh,)).fetchone()[0] is None


def test_deletion_audit_ninety_days_separate_from_task_logs(cleanup_db):
    from app import cleanup
    db=cleanup_db;uid,tid=queued(db)
    for kind,days in [('DELETION_90D',89),('DELETION_90D',91),('TASK_30D',31)]:
        db.execute("INSERT INTO audit_events(target_user_id,event_type,result,retention_class,created_at) VALUES (%s,'SYNTHETIC','OK',%s,now()-%s*interval '1 day')",(uid,kind,days))
    cleanup.run_once()
    rows=db.execute("SELECT retention_class FROM audit_events WHERE event_type='SYNTHETIC'").fetchall()
    assert rows==[('DELETION_90D',)]


def test_failed_cleanup_escalates_after_three_retries_not_first_attempt(cleanup_db,monkeypatch):
    from app import cleanup,tasks
    db=cleanup_db;uid,tid=queued(db)
    asset(db,uid,tid,'PROCESSING_WORKSPACE','invalid-unowned-path')
    cleanup.request_delete(tid,uid);identifier=job(db,tid)[0]
    now=[datetime.now(timezone.utc)+timedelta(seconds=2)]
    monkeypatch.setattr(tasks,'utcnow',lambda:now[0])
    for attempt in range(1,6):
        cleanup.process_job(identifier)
        state=job(db,tid)
        assert state[2]==attempt
        if attempt<=4:assert state[1]!='MANUAL_REQUIRED'
        now[0]+=timedelta(minutes=1,seconds=1)
    assert job(db,tid)[1]=='MANUAL_REQUIRED'
    assert db.execute('SELECT count(*) FROM cleanup_alerts WHERE cleanup_job_id=%s AND resolved_at IS NULL',(identifier,)).fetchone()[0]>=1


def test_thirty_minute_failure_alert_and_admin_retrigger_are_audited(cleanup_db,monkeypatch):
    from app import cleanup,tasks
    db=cleanup_db;uid,tid=queued(db);cleanup.request_delete(tid,uid)
    identifier=job(db,tid)[0]
    db.execute("UPDATE cleanup_jobs SET started_at=now()-interval '31 minutes' WHERE id=%s",(identifier,))
    # Unsupported asset prevents accidental completion before escalation.
    asset(db,uid,tid,'PROCESSING_WORKSPACE','invalid-unowned-path')
    cleanup.process_job(identifier)
    assert job(db,tid)[1]=='MANUAL_REQUIRED'
    admin=identity(db,'ADMIN');cleanup.retry_job(identifier,admin)
    assert job(db,tid)[1]=='PENDING'
    assert db.execute("SELECT count(*) FROM audit_events WHERE actor_user_id=%s AND retention_class='DELETION_90D'",(admin,)).fetchone()[0]>=1
    before=job(db,tid)[2]
    cleanup.process_job(identifier)
    assert job(db,tid)[2]==before+1 # persisted admin intent survives worker dispatch
    assert cleanup.list_jobs(uid)[0]['online_complete'] is False


def test_http_delete_requires_csrf_and_immediately_denies_content(cleanup_db):
    from app import auth
    db=cleanup_db;uid,tid,generation=claimed(db);publish(tid,generation)
    async def scenario():
        async with client() as c:
            authenticated(db,c,uid)
            assert (await c.delete(f'/tasks/{tid}')).status_code==403
            token=c.cookies.get(auth.COOKIE_NAME)
            response=await c.delete(f'/tasks/{tid}',headers={'Origin':'http://127.0.0.1:8000','X-CSRF-Token':auth.csrf_token(token)})
            assert response.status_code==202,response.text
            assert response.json()['online_complete'] is False
            assert (await c.get('/tasks')).json()==[]
            for suffix in ('','/result?language=zh','/export?language=en','/video'):
                assert (await c.get(f'/tasks/{tid}'+suffix)).status_code in (403,404)
            assert (await c.get('/deletions')).status_code==200
    asyncio.run(scenario())


def test_paused_worker_cannot_create_workspace_after_deletion_cleanup(cleanup_db,monkeypatch):
    from app import cleanup,processing,tasks
    from app.worker import WorkerContext
    db=cleanup_db;uid,tid,generation=claimed(db)
    context=WorkerContext({'id':str(tid),'generation':generation},'result-test-worker')
    real_write=context.fenced_write
    calls=[0]
    def pause_after_registration(callback):
        result=real_write(callback);calls[0]+=1
        if calls[0]==1:
            cleanup.request_delete(tid,uid)
            db.execute('UPDATE cleanup_jobs SET worker_safe_after=now() WHERE task_id=%s',(tid,))
            cleanup.process_job(job(db,tid)[0])
            assert cleanup.list_jobs(uid)[0]['online_complete'] is True
        return result
    monkeypatch.setattr(context,'fenced_write',pause_after_registration)
    with pytest.raises((HTTPException,RuntimeError)):
        with processing._workspace(context):pytest.fail('Deleted attempt cannot start workspace work')
    root=processing._root()
    assert not list(root.iterdir())
    assert db.execute('SELECT deleted_at FROM tasks WHERE id=%s',(tid,)).fetchone()[0]


def test_expired_task_bodies_and_logs_are_pruned_despite_external_cleanup_failure(cleanup_db):
    from app import cleanup
    db=cleanup_db;uid,tid,generation=claimed(db);publish(tid,generation)
    db.execute("UPDATE tasks SET created_at=now()-interval '31 days' WHERE id=%s",(tid,))
    asset(db,uid,tid,'UNKNOWN','unowned-unremovable-path')
    cleanup.run_once()
    assert cleanup.list_jobs(uid)[0]['online_complete'] is False
    assert db.execute('SELECT count(*) FROM task_results WHERE task_id=%s',(tid,)).fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM task_attempts WHERE task_id=%s',(tid,)).fetchone()[0]==0
    assert db.execute("SELECT count(*) FROM audit_events WHERE task_id=%s AND retention_class='TASK_30D'",(tid,)).fetchone()[0]==0


@pytest.mark.parametrize('elapsed_seconds,expect_alert',[(1800,False),(1801,True)])
def test_long_worker_lease_does_not_postpone_cleanup_deadline_alert(cleanup_db,monkeypatch,elapsed_seconds,expect_alert):
    from app import cleanup,tasks
    db=cleanup_db;uid,tid,generation=claimed(db)
    future=datetime.now(timezone.utc)+timedelta(hours=1)
    db.execute('UPDATE tasks SET lease_expires_at=%s WHERE id=%s',(future,tid))
    cleanup.request_delete(tid,uid)
    identifier=job(db,tid)[0]
    now=datetime.now(timezone.utc)+timedelta(seconds=1)
    monkeypatch.setattr(tasks,'utcnow',lambda:now)
    db.execute('UPDATE cleanup_jobs SET started_at=%s,next_attempt_at=%s WHERE id=%s',(now-timedelta(seconds=elapsed_seconds),future,identifier))
    cleanup.run_once()
    row=job(db,tid)
    assert row[2]==0 # waiting on a live old lease never performs physical cleanup
    assert (row[1]=='MANUAL_REQUIRED') is expect_alert
    count=db.execute("SELECT count(*) FROM cleanup_alerts WHERE cleanup_job_id=%s AND alert_code='CLEANUP_TIME_LIMIT' AND resolved_at IS NULL",(identifier,)).fetchone()[0]
    assert count==int(expect_alert)
