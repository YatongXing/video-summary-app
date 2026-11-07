"""Real PG cleanup state and failure oracles; S3 fake is explicit."""
import asyncio,json,os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
import pytest
from botocore.exceptions import ClientError
from cr_wp04_fixtures import wp04_db,queued,object_asset,row,claimed,publish,authenticated,client

class Store:
 def __init__(self,code=None,visible=False,versioned=False):self.code=code;self.visible=visible;self.versioned=versioned;self.deleted=[]
 def list_multipart_uploads(self,**kw):return {'Uploads':[]}
 def head_bucket(self,**kw):
  if self.code:raise ClientError({'Error':{'Code':self.code}},'HeadBucket')
 def get_bucket_versioning(self,**kw):return {'Status':'Enabled'} if self.versioned else {}
 def delete_object(self,**kw):self.deleted.append(kw)
 def head_object(self,**kw):
  if self.visible:return {'ContentLength':1}
  raise ClientError({'Error':{'Code':'NoSuchKey'}},'HeadObject')

def storage(monkeypatch,obj):
 from app import object_store
 monkeypatch.setenv('S3_ENDPOINT_URL','http://wp04-synthetic:9000')
 monkeypatch.setenv('S3_BUCKET','wp04-synthetic')
 monkeypatch.setattr(object_store,'client',lambda:obj)
 if hasattr(object_store,'cleanup_client'):monkeypatch.setattr(object_store,'cleanup_client',lambda:obj)
 return obj

def test_verified_empty_job_completes_not_just_accepted(wp04_db):
 from app import cleanup
 db=wp04_db;u,t,g=claimed(db);publish(t,g)
 accepted=cleanup.request_delete(t,u)
 assert not accepted.get('online_complete',False)
 result=cleanup.process_job(row(db,t)['id'])
 assert row(db,t)['status']=='COMPLETED'
 assert row(db,t)['online_completed_at'] and row(db,t)['completed_at']
 assert db.execute("SELECT count(*) FROM task_results WHERE task_id=%s",(t,)).fetchone()==(0,)

@pytest.mark.parametrize('code',['NoSuchBucket','403','AccessDenied'])
def test_bucket_failures_never_mean_object_absent(wp04_db,monkeypatch,code):
 from app import cleanup
 db=wp04_db;obj=storage(monkeypatch,Store(code));u,t=queued(db);aid=object_asset(db,u,t)
 cleanup.request_delete(t,u);cleanup.process_job(row(db,t)['id'])
 assert row(db,t)['status']=='BLOCKED' and row(db,t)['blocked_reason']=='CONFIG'
 assert not obj.deleted
 assert db.execute("SELECT cleanup_state FROM media_assets WHERE id=%s",(aid,)).fetchone()[0]!='DELETED'

def test_versioned_bucket_stops_before_any_delete(wp04_db,monkeypatch):
 from app import cleanup
 db=wp04_db;obj=storage(monkeypatch,Store(versioned=True));u,t=queued(db);object_asset(db,u,t)
 cleanup.request_delete(t,u);cleanup.process_job(row(db,t)['id'])
 assert not obj.deleted and row(db,t)['status']=='BLOCKED'

def test_backup_waits_for_approval_but_online_content_clears(wp04_db,monkeypatch):
 from app import cleanup
 db=wp04_db;obj=storage(monkeypatch,Store());u,t,g=claimed(db);publish(t,g)
 aid=object_asset(db,u,t,kind='BACKUP')
 cleanup.request_delete(t,u);cleanup.process_job(row(db,t)['id'])
 r=row(db,t)
 assert r['status']=='BLOCKED' and r['blocked_reason']=='LEGACY_APPROVAL'
 assert r['online_completed_at'] and r['completed_at'] is None
 assert db.execute("SELECT count(*) FROM task_results WHERE task_id=%s",(t,)).fetchone()==(0,)
 assert not obj.deleted

def test_missing_producer_evidence_never_runs_physical_delete(wp04_db,monkeypatch):
 from app import cleanup
 db=wp04_db;obj=storage(monkeypatch,Store());u,t=queued(db);object_asset(db,u,t)
 (Path(os.environ['DELETION_JOURNAL_DIR'])/'runtime'/'producer-ready').unlink()
 cleanup.request_delete(t,u);cleanup.process_job(row(db,t)['id'])
 assert not obj.deleted and row(db,t)['status']=='BLOCKED'
 assert row(db,t)['blocked_reason']=='PRODUCER_UNKNOWN'

def test_unfinished_old_intent_survives_ninety_day_prune(wp04_db):
 from app import cleanup,deletion_control,tasks
 db=wp04_db;u,t=queued(db);cleanup.request_delete(t,u)
 f=Path(os.environ['DELETION_JOURNAL_DIR'])/(str(t)+'.json')
 v=json.loads(f.read_text());v['requested_at']=(tasks.utcnow()-timedelta(days=91)).isoformat();f.write_text(json.dumps(v))
 deletion_control.prune_journal()
 assert f.exists() and deletion_control.blocked(t)

def test_retired_cleanup_http_interfaces_reject_real_admin(wp04_db):
 from app import auth
 db=wp04_db;a=db.execute("INSERT INTO users(email_normalized,password_hash,role) VALUES(%s,'synthetic','ADMIN') RETURNING id",('wp04-'+uuid4().hex+'@example.invalid',)).fetchone()[0]
 async def run():
  async with client() as c:
   authenticated(db,c,a)
   assert (await c.get('/deletions')).status_code==410
   assert (await c.get('/admin/cleanup-jobs')).status_code==410
   assert (await c.post('/admin/cleanup-jobs/'+str(uuid4())+'/retry',headers={'X-CSRF-Token':auth.csrf_token(c.cookies.get(auth.COOKIE_NAME))})).status_code==410
 asyncio.run(run())

def test_real_private_s3_delete_only_target(wp04_db,monkeypatch):
 from app import cleanup,object_store
 endpoint=os.environ.get('WP04_S3_ENDPOINT')
 if not endpoint:pytest.skip('Explicit private WP04 S3 test resource required')
 monkeypatch.setenv('S3_ENDPOINT_URL',endpoint)
 monkeypatch.setenv('S3_ACCESS_KEY',os.environ.get('WP07A_SYNTHETIC_S3_ACCESS_KEY','wp04synthetic'));monkeypatch.setenv('S3_SECRET_KEY',os.environ.get('WP07A_SYNTHETIC_S3_SECRET_KEY','wp04-synthetic-storage'))
 bucket='wp04-'+uuid4().hex;monkeypatch.setenv('S3_BUCKET',bucket)
 s3=object_store.client();s3.create_bucket(Bucket=bucket)
 db=wp04_db;u,t=queued(db);u2,t2=queued(db)
 key=f'originals/{u}/{uuid4()}';other=f'originals/{u2}/{uuid4()}'
 try:
  s3.put_object(Bucket=bucket,Key=key,Body=b'only-synthetic-target')
  s3.put_object(Bucket=bucket,Key=other,Body=b'other-task-kept')
  object_asset(db,u,t,key,bucket=bucket);object_asset(db,u2,t2,other,bucket=bucket)
  cleanup.request_delete(t,u);cleanup.process_job(row(db,t)['id'])
  assert row(db,t)['status']=='COMPLETED'
  with pytest.raises(ClientError):s3.head_object(Bucket=bucket,Key=key)
  assert s3.get_object(Bucket=bucket,Key=other)['Body'].read()==b'other-task-kept'
 finally:
  for name in (key,other):s3.delete_object(Bucket=bucket,Key=name)
  s3.delete_bucket(Bucket=bucket)

@pytest.mark.parametrize('autocommit',[False,True])
def test_stale_maintenance_version_and_audit_commit_failure(wp04_db,monkeypatch,autocommit):
 import psycopg
 from psycopg.rows import dict_row
 from app import cleanup,cleanup_ops,object_store
 db=wp04_db;storage(monkeypatch,Store('AccessDenied'));u,t=queued(db);object_asset(db,u,t)
 cleanup.request_delete(t,u);cleanup.process_job(row(db,t)['id']);j=row(db,t)
 with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,autocommit=autocommit) as c:
  with pytest.raises(cleanup_ops.OpsError) as e:cleanup_ops.retry(j['id'],j['job_version']-1,connection=c)
  assert e.value.exit_code==5
 obj=Store();storage(monkeypatch,obj)
 db.execute("""CREATE FUNCTION reject_ops() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic audit commit failure'; END $$""")
 db.execute("""CREATE CONSTRAINT TRIGGER reject_ops AFTER INSERT ON audit_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_ops()""")
 with pytest.raises(psycopg.Error):
  with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,autocommit=autocommit) as c:cleanup_ops.retry(j['id'],j['job_version'],connection=c)
 assert row(db,t)['job_version']==j['job_version'] and row(db,t)['status']=='BLOCKED'
 assert not obj.deleted

def test_health_detects_stale_loop_and_pending_import(wp04_db):
 import time
 from app import cleanup_ops
 root=Path(os.environ['DELETION_JOURNAL_DIR'])/'runtime'
 (root/'cleanup-io.json').write_text(json.dumps({'active_io_started':None,'last_io_completed':None}))
 (root/'cleanup-progress.json').write_text(json.dumps({'last_loop_completed':time.time()-61}))
 assert cleanup_ops.health()['healthy'] is False
 (root/'cleanup-progress.json').write_text(json.dumps({'last_loop_completed':time.time(),'active_io_started':None}))
 assert cleanup_ops.health()['healthy'] is True
 u,t=queued(wp04_db)
 wp04_db.execute("INSERT INTO input_imports(id,user_id,object_key,state) VALUES(%s,%s,%s,'CLEANUP_REQUIRED')",(uuid4(),u,'originals/synthetic'))
 assert cleanup_ops.health()['healthy'] is False


def test_budget_continuation_preserves_failure_history(wp04_db,monkeypatch):
 from app import cleanup,tasks
 db=wp04_db;obj=storage(monkeypatch,Store());u,t=queued(db)
 for _ in range(21):object_asset(db,u,t,key='derived/'+str(t)+'/'+uuid4().hex)
 cleanup.request_delete(t,u);j=row(db,t)
 db.execute("UPDATE cleanup_jobs SET consecutive_failures=3,job_version=job_version+1 WHERE id=%s",(j['id'],))
 cleanup.process_job(j['id']);a=row(db,t)
 assert len(obj.deleted)==20 and a['status']!='COMPLETED'
 assert a['consecutive_failures']==3
 cleanup.process_job(j['id']);assert len(obj.deleted)==20
 now=tasks.utcnow()+timedelta(seconds=75);monkeypatch.setattr(tasks,'utcnow',lambda:now)
 cleanup.process_job(j['id'])
 assert len(obj.deleted)==21 and row(db,t)['status']=='COMPLETED'

def test_io_completion_cannot_overwrite_newer_block(wp04_db,monkeypatch):
 from app import cleanup
 db=wp04_db;obj=storage(monkeypatch,Store());u,t=queued(db);aid=object_asset(db,u,t)
 cleanup.request_delete(t,u);j=row(db,t)
 original=obj.delete_object
 def block(**kw):
  original(**kw)
  db.execute("UPDATE cleanup_jobs SET status='BLOCKED',blocked_reason='UNSAFE',job_version=job_version+1 WHERE id=%s",(j['id'],))
 obj.delete_object=block
 cleanup.process_job(j['id'])
 assert row(db,t)['status']=='BLOCKED' and row(db,t)['blocked_reason']=='UNSAFE'
 assert db.execute("SELECT cleanup_state FROM media_assets WHERE id=%s",(aid,)).fetchone()[0]!='DELETED'

def test_expired_unknown_journal_is_not_inferred_complete(wp04_db):
 from app import deletion_control,tasks
 root=Path(os.environ['DELETION_JOURNAL_DIR']);task=uuid4();user=uuid4()
 path=root/(str(task)+'.json')
 path.write_text(json.dumps({'task_id':str(task),'user_id':str(user),'requested_at':(tasks.utcnow()-timedelta(days=91)).isoformat()}))
 deletion_control.prune_journal()
 assert path.exists()

def test_arbitrary_content_locator_is_removed_only_after_real_verification(wp04_db,monkeypatch):
 from app import cleanup
 db=wp04_db;obj=storage(monkeypatch,Store());u,t=queued(db)
 key='evaluation/private-original-file-name'
 aid=object_asset(db,u,t,key,kind='EVALUATION_COPY')
 cleanup.request_delete(t,u);cleanup.process_job(row(db,t)['id'])
 assert [x['Key'] for x in obj.deleted]==[key]
 assert row(db,t)['status']=='COMPLETED'
 assert db.execute('SELECT count(*) FROM media_assets WHERE id=%s',(aid,)).fetchone()==(0,)

def test_completed_window_uses_completion_and_keeps_fresh_audit(wp04_db):
 from app import cleanup,deletion_control,tasks
 db=wp04_db;u,t=queued(db);cleanup.request_delete(t,u);cleanup.process_job(row(db,t)['id'])
 db.execute("UPDATE tasks SET created_at=clock_timestamp()-interval '120 days' WHERE id=%s",(t,))
 db.execute("UPDATE cleanup_jobs SET started_at=clock_timestamp()-interval '100 days',job_version=job_version+1 WHERE task_id=%s",(t,))
 cleanup.run_once()
 assert row(db,t) is not None,'Old start must not erase newly completed controls'
 db.execute("UPDATE cleanup_jobs SET online_completed_at=clock_timestamp()-interval '91 days',completed_at=clock_timestamp()-interval '91 days',job_version=job_version+1 WHERE task_id=%s",(t,))
 f=Path(os.environ['DELETION_JOURNAL_DIR'])/(str(t)+'.json')
 v=json.loads(f.read_text());v['requested_at']=(tasks.utcnow()-timedelta(days=100)).isoformat();f.write_text(json.dumps(v))
 db.execute("INSERT INTO audit_events(task_id,target_user_id,event_type,result,retention_class) VALUES(%s,%s,'RECENT_DELETE_EVENT','OK','DELETION_90D')",(t,u))
 deletion_control.prune_journal();assert not f.exists()
 cleanup.run_once()
 assert db.execute('SELECT count(*) FROM tasks WHERE id=%s',(t,)).fetchone()==(0,)
 assert db.execute("SELECT count(*) FROM audit_events WHERE task_id=%s AND event_type='RECENT_DELETE_EVENT'",(t,)).fetchone()==(1,)

def test_active_io_cannot_be_hidden_by_fresh_scanner(wp04_db):
 import time
 from app import cleanup,cleanup_ops
 root=Path(os.environ['DELETION_JOURNAL_DIR'])/'runtime'
 (root/'cleanup-io.json').write_text(json.dumps({'active_io_started':time.time()-61,'active_job_id':str(uuid4()),'last_io_completed':time.time()-80}))
 from app.task_locks import cleanup_global
 with cleanup_global():cleanup.run_once()
 value=json.loads((root/'cleanup-io.json').read_text())
 assert value['active_io_started']<time.time()-60
 assert cleanup_ops.health()['healthy'] is False

def test_import_location_drift_never_deletes_object_or_file(wp04_db,monkeypatch):
 from app import ingest,object_store,auth
 db=wp04_db;u,t=queued(db);token=uuid4().hex
 session=db.execute("INSERT INTO sessions(user_id,token_hash,expires_at) VALUES(%s,%s,clock_timestamp()+interval '1 hour') RETURNING id",(u,auth.digest(token))).fetchone()[0]
 principal=auth.Principal(u,'synthetic@example.invalid','USER',session)
 monkeypatch.setenv('IMPORT_STAGING_DIR',str(Path(os.environ['DELETION_JOURNAL_DIR']).parent/'staging'))
 ident,key,path=ingest.begin_import(principal);path.write_bytes(b'synthetic incomplete media')
 db.execute("UPDATE input_imports SET state='CLEANUP_REQUIRED',expires_at=clock_timestamp() WHERE id=%s",(ident,))
 deleted=[];monkeypatch.setattr(object_store,'delete_original',lambda key:deleted.append(key))
 monkeypatch.setenv('S3_ENDPOINT_URL','http://other-backend.invalid:9000')
 ingest.recover_staging()
 assert path.exists() and not deleted
 assert db.execute("SELECT state FROM input_imports WHERE id=%s",(ident,)).fetchone()==('CLEANUP_REQUIRED',)

def test_real_s3_multipart_parts_are_removed_without_touching_prefix_neighbor(wp04_db,monkeypatch):
 from app import cleanup,object_store
 endpoint=os.environ.get('WP04_S3_ENDPOINT')
 if not endpoint:pytest.skip('Explicit private WP04 S3 resource required')
 monkeypatch.setenv('S3_ENDPOINT_URL',endpoint);monkeypatch.setenv('S3_ACCESS_KEY',os.environ.get('WP07A_SYNTHETIC_S3_ACCESS_KEY','wp04synthetic'));monkeypatch.setenv('S3_SECRET_KEY',os.environ.get('WP07A_SYNTHETIC_S3_SECRET_KEY','wp04-synthetic-storage'))
 bucket='wp04-'+uuid4().hex;monkeypatch.setenv('S3_BUCKET',bucket)
 s3=object_store.client();s3.create_bucket(Bucket=bucket)
 db=wp04_db;u,t=queued(db);key=f'originals/{u}/{uuid4()}';neighbor=key+'-other'
 uploads=[]
 try:
  for name in (key,neighbor):
   ident=s3.create_multipart_upload(Bucket=bucket,Key=name)['UploadId'];uploads.append((name,ident))
   s3.upload_part(Bucket=bucket,Key=name,UploadId=ident,PartNumber=1,Body=b'synthetic-private-part')
  object_asset(db,u,t,key,bucket=bucket);cleanup.request_delete(t,u);cleanup.process_job(row(db,t)['id'])
  assert row(db,t)['status']=='COMPLETED'
  remaining=s3.list_multipart_uploads(Bucket=bucket).get('Uploads',[])
  assert not any(x['Key']==key for x in remaining)
  assert any(x['Key']==neighbor for x in remaining)
 finally:
  for name,ident in uploads:
   try:s3.abort_multipart_upload(Bucket=bucket,Key=name,UploadId=ident)
   except ClientError:pass
  s3.delete_bucket(Bucket=bucket)

def test_single_io_budget_stops_next_call_without_failure_reset(wp04_db,monkeypatch):
 import time
 from app import cleanup
 db=wp04_db;obj=storage(monkeypatch,Store());u,t=queued(db);object_asset(db,u,t)
 cleanup.request_delete(t,u);j=row(db,t)
 db.execute("UPDATE cleanup_jobs SET consecutive_failures=3,job_version=job_version+1 WHERE id=%s",(j['id'],))
 actual=time.monotonic;offset=[0]
 monkeypatch.setattr(cleanup.time,'monotonic',lambda:actual()+offset[0])
 def consume(**kw):offset[0]=31
 obj.head_bucket=consume
 cleanup.process_job(j['id'])
 assert not obj.deleted
 assert row(db,t)['status']!='COMPLETED' and row(db,t)['consecutive_failures']==3

def test_upload_endpoint_changes_after_put_never_binds_new_task(wp04_db,monkeypatch):
 import threading
 from types import SimpleNamespace
 from app import ingest,object_store,auth,media
 db=wp04_db;u,t=queued(db);token=uuid4().hex
 session=db.execute("INSERT INTO sessions(user_id,token_hash,expires_at) VALUES(%s,%s,clock_timestamp()+interval '1 hour') RETURNING id",(u,auth.digest(token))).fetchone()[0]
 principal=auth.Principal(u,'synthetic@example.invalid','USER',session)
 monkeypatch.setenv('IMPORT_STAGING_DIR',str(Path(os.environ['DELETION_JOURNAL_DIR']).parent/'staging'))
 ident,key,path=ingest.begin_import(principal);path.write_bytes(b'synthetic-already-validated-source')
 deleted=[]
 def put(*args):monkeypatch.setenv('S3_ENDPOINT_URL','http://other-backend.invalid:9000')
 monkeypatch.setattr(object_store,'put_original',put)
 monkeypatch.setattr(object_store,'delete_original',lambda key:deleted.append(key))
 before=db.execute('SELECT count(*) FROM tasks').fetchone()
 with pytest.raises(media.InputError):ingest.finish_import(principal,ident,key,path,{'input_kind':'LOCAL','media':SimpleNamespace(duration_seconds=1,has_audio=False)},'synthetic.mp4',threading.Event())
 assert db.execute('SELECT count(*) FROM tasks').fetchone()==before
 assert not deleted and path.exists()
 assert db.execute("SELECT state FROM input_imports WHERE id=%s",(ident,)).fetchone()==('CLEANUP_REQUIRED',)

def test_intent_commit_failure_replays_without_restart(wp04_db):
 import psycopg
 from app import cleanup,deletion_control
 db=wp04_db;u,t,g=claimed(db);publish(t,g)
 db.execute("""CREATE FUNCTION reject_cleanup_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic deferred failure'; END $$""")
 db.execute("""CREATE CONSTRAINT TRIGGER reject_cleanup_commit AFTER INSERT ON cleanup_jobs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_cleanup_commit()""")
 with pytest.raises(psycopg.Error):cleanup.request_delete(t,u)
 assert deletion_control.blocked(t)
 assert db.execute('SELECT count(*) FROM cleanup_jobs WHERE task_id=%s',(t,)).fetchone()==(0,)
 assert db.execute('SELECT deletion_requested_at FROM tasks WHERE id=%s',(t,)).fetchone()==(None,)
 db.execute('DROP TRIGGER reject_cleanup_commit ON cleanup_jobs')
 cleanup.run_once()
 assert row(db,t) is not None
 assert db.execute('SELECT deletion_requested_at FROM tasks WHERE id=%s',(t,)).fetchone()[0]
 assert db.execute('SELECT count(*) FROM task_results WHERE task_id=%s',(t,)).fetchone()==(0,)

def test_blocked_expired_task_still_purges_sql_without_claiming_physical_success(wp04_db):
 from app import cleanup
 db=wp04_db;u,t,g=claimed(db);publish(t,g);object_asset(db,u,t)
 cleanup.request_delete(t,u)
 db.execute("UPDATE tasks SET created_at=clock_timestamp()-interval '31 days' WHERE id=%s",(t,))
 db.execute("UPDATE cleanup_jobs SET status='BLOCKED',blocked_reason='UNSAFE',job_version=job_version+1 WHERE task_id=%s",(t,))
 cleanup.run_once()
 assert db.execute('SELECT count(*) FROM task_results WHERE task_id=%s',(t,)).fetchone()==(0,)
 assert row(db,t)['status']=='BLOCKED' and row(db,t)['completed_at'] is None
 assert db.execute("SELECT count(*) FROM media_assets WHERE task_id=%s AND cleanup_state<>'DELETED'",(t,)).fetchone()[0]>0
