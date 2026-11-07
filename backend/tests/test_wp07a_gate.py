"""Independent gate constraints on isolated PG and synthetic content adapters."""
import asyncio,json,os
from pathlib import Path
from uuid import uuid4
import pytest
from app import auth,maintenance,results,deletion_control
from test_results import result_db,identity,claimed,publish,authenticated,client
from test_cr002 import Store,asset

@pytest.fixture
def gated(result_db,monkeypatch,tmp_path):
 monkeypatch.setenv('DELETION_JOURNAL_DIR',str(tmp_path/'journal'))
 monkeypatch.setenv('MAINTENANCE_GATE_REQUIRED','false')
 deletion_control.initialize()
 runtime=deletion_control._root()/'runtime'
 runtime.mkdir()
 deployment=gated_deployment_id(result_db)
 deletion_control._write(runtime/'producer-ready',{'deployment_id':deployment})
 with auth.connect() as c:maintenance.set_state('CLOSED',connection=c)
 monkeypatch.setenv('MAINTENANCE_GATE_REQUIRED','true')
 return result_db

def gated_deployment_id(db):
 return str(db.execute('SELECT deployment_id FROM deletion_control_state WHERE singleton').fetchone()[0])

def set_gate(value):
 with auth.connect() as c:return maintenance.set_state(value,connection=c)

@pytest.mark.parametrize('mode',['missing','corrupt','identity','state','symlink'])
def test_gate_untrusted_never_opens(gated,mode,tmp_path):
 p=deletion_control._root()/'runtime/maintenance.json'
 if mode=='missing':p.unlink()
 elif mode=='corrupt':p.write_text('{broken')
 elif mode=='symlink':
  p.unlink();p.symlink_to(tmp_path/'missing-target')
 else:
  value=json.loads(p.read_text());value['deployment_id' if mode=='identity' else 'state']=str(uuid4()) if mode=='identity' else 'UNKNOWN';p.write_text(json.dumps(value))
 assert maintenance.ready() is False
 with pytest.raises(deletion_control.JournalError):maintenance.ensure_open()
 assert not p.exists() if mode in ['missing','symlink'] else True

def test_closed_blocks_every_public_route_live_remains(gated):
 async def run():
  async with client() as c:
   for method,path in [('GET','/tasks'),('GET','/quota'),('POST','/auth/login'),('POST','/inputs/upload'),('GET','/admin/tasks'),('GET','/health/ready')]:
    assert (await c.request(method,path)).status_code==503,(method,path)
   assert (await c.get('/health/live')).status_code==200
 asyncio.run(run())

def test_open_reconnect_closed_and_reopen(gated):
 assert not maintenance.ready();set_gate('OPEN');assert maintenance.ready()
 assert maintenance.read_state()['state']=='OPEN'
 set_gate('CLOSED');assert not maintenance.ready();set_gate('OPEN');assert maintenance.ready()

@pytest.mark.parametrize('phase',['head','read'])
def test_video_stops_on_gate_during_storage_io(gated,monkeypatch,phase):
 set_gate('OPEN');u,t,g=claimed(gated);asset(gated,u,t)
 def close():set_gate('CLOSED')
 monkeypatch.setattr(results.object_store,'client',lambda:Store(close,phase=='head'))
 async def run():
  async with client() as c:
   authenticated(gated,c,u);v=await c.get(f'/tasks/{t}/video')
   if phase=='head':assert v.status_code==503
   else:assert v.content==b''
 asyncio.run(run())

def test_no_claim_or_publish_when_closed(gated):
 from app import queue
 set_gate('OPEN');u,t,g=claimed(gated)
 set_gate('CLOSED')
 with pytest.raises(deletion_control.JournalError):queue.claim_next('closed-worker')
 with pytest.raises(deletion_control.JournalError):publish(t,g)
 assert gated.execute('SELECT count(*) FROM task_results WHERE task_id=%s',(t,)).fetchone()[0]==0
 assert gated.execute('SELECT status FROM tasks WHERE id=%s',(t,)).fetchone()[0]=='PROCESSING'
 set_gate('OPEN');publish(t,g)
 assert gated.execute('SELECT status FROM tasks WHERE id=%s',(t,)).fetchone()[0]=='SUCCEEDED'


def test_publish_close_after_last_state_write_rolls_back(gated,monkeypatch):
 set_gate('OPEN');u,t,g=claimed(gated);original=__import__('app.tasks',fromlist=['_finish_locked'])._finish_locked
 def close(*args,**kwargs):
  result=original(*args,**kwargs);set_gate('CLOSED');return result
 monkeypatch.setattr('app.tasks._finish_locked',close)
 with pytest.raises(deletion_control.JournalError):publish(t,g)
 assert gated.execute('SELECT count(*) FROM task_results WHERE task_id=%s',(t,)).fetchone()[0]==0
 assert gated.execute('SELECT status FROM tasks WHERE id=%s',(t,)).fetchone()[0]=='PROCESSING'

def test_claim_close_after_charge_rolls_back(gated,monkeypatch):
 from app import tasks,queue
 set_gate('OPEN');u=identity(gated);t=gated.execute("INSERT INTO tasks(user_id,input_kind) VALUES(%s,'LOCAL') RETURNING id",(u,)).fetchone()[0];before=gated.execute('SELECT credits_available FROM users WHERE id=%s',(u,)).fetchone()
 original=tasks._start_locked
 def close(*args,**kwargs):
  result=original(*args,**kwargs);set_gate('CLOSED');return result
 monkeypatch.setattr(tasks,'_start_locked',close)
 with pytest.raises(deletion_control.JournalError):queue.claim_next('late-gate-worker')
 assert gated.execute('SELECT status FROM tasks WHERE id=%s',(t,)).fetchone()[0]=='QUEUED'
 assert gated.execute('SELECT credits_available FROM users WHERE id=%s',(u,)).fetchone()==before
 assert gated.execute('SELECT count(*) FROM task_attempts WHERE task_id=%s',(t,)).fetchone()[0]==0

def test_delete_close_after_audit_rolls_back_db_keeps_intent(gated,monkeypatch):
 from app import cleanup
 set_gate('OPEN');u,t,g=claimed(gated);original=cleanup._audit
 def close(*args,**kwargs):
  result=original(*args,**kwargs);set_gate('CLOSED');return result
 monkeypatch.setattr(cleanup,'_audit',close)
 with pytest.raises(deletion_control.JournalError):cleanup.request_delete(t,u)
 assert gated.execute('SELECT deletion_requested_at FROM tasks WHERE id=%s',(t,)).fetchone()[0] is None
 assert gated.execute('SELECT count(*) FROM cleanup_jobs WHERE task_id=%s',(t,)).fetchone()[0]==0
 assert deletion_control.blocked(t)


def test_frame_close_during_read_rejects_evidence(gated,monkeypatch):
 import threading
 from app import processing
 set_gate('OPEN');calls=[]
 class Context:
  cancelled=threading.Event()
  def checkpoint(self):return True
 class Frame:
  def read_bytes(self):calls.append(1);set_gate('CLOSED');return b'frame'
 with pytest.raises(deletion_control.JournalError):processing._read_frame(Context(),Frame())
 assert len(calls)==1
 with pytest.raises(deletion_control.JournalError):processing._read_frame(Context(),Frame())
 assert len(calls)==1

def test_audio_close_during_read_closes_fd(gated):
 import threading,io
 from app import processing
 set_gate('OPEN');calls=[]
 class Context:
  cancelled=threading.Event()
  def checkpoint(self):return True
 class Stream(io.BytesIO):
  def read(self,size=-1):calls.append(1);v=super().read(size);set_gate('CLOSED');return v
 class Audio:
  def open(self,mode):self.stream=Stream(b'abc');return self.stream
 audio=Audio();reader=processing._AudioReader(Context(),audio)
 with pytest.raises(deletion_control.JournalError):reader.read()
 assert reader.closed and len(calls)==1
 with pytest.raises(deletion_control.JournalError):reader.read()
 assert len(calls)==1


@pytest.mark.parametrize('operation,status',[('cancel','QUEUED'),('retry','FAILED')])
def test_cancel_and_retry_last_metadata_gate_rolls_back(gated,monkeypatch,operation,status):
 from app import tasks
 set_gate('OPEN')
 user=identity(gated)
 task=gated.execute('INSERT INTO tasks(user_id,input_kind,status) VALUES(%s,%s,%s) RETURNING id',(user,'LOCAL',status)).fetchone()[0]
 original=tasks._metadata
 def close(*args,**kwargs):
  result=original(*args,**kwargs)
  set_gate('CLOSED')
  return result
 monkeypatch.setattr(tasks,'_metadata',close)
 with pytest.raises(deletion_control.JournalError):
  if operation=='cancel':tasks.cancel_task(task,user)
  else:tasks.request_retry(task,user,uuid4())
 assert gated.execute('SELECT status,retry_count FROM tasks WHERE id=%s',(task,)).fetchone()==(status,0)
 assert gated.execute('SELECT count(*) FROM retry_events WHERE task_id=%s',(task,)).fetchone()==(0,)
 assert gated.execute('SELECT count(*) FROM audit_events WHERE task_id=%s',(task,)).fetchone()==(0,)


def test_runtime_replay_late_close_rolls_back_owner_can_replay_closed(gated,monkeypatch):
 from app import cleanup,tasks
 set_gate('OPEN')
 user,task,_=claimed(gated)
 deletion_control.record_intent(task,user,tasks.utcnow())
 original=cleanup._audit
 def close(*args,**kwargs):
  result=original(*args,**kwargs)
  set_gate('CLOSED')
  return result
 with monkeypatch.context() as context:
  context.setattr(cleanup,'_audit',close)
  with pytest.raises(deletion_control.JournalError):deletion_control.replay_journal()
 assert gated.execute('SELECT deletion_requested_at FROM tasks WHERE id=%s',(task,)).fetchone()==(None,)
 assert gated.execute('SELECT count(*) FROM cleanup_jobs WHERE task_id=%s',(task,)).fetchone()==(0,)
 assert deletion_control.blocked(task)
 assert maintenance.read_state()['state']=='CLOSED'
 monkeypatch.setenv('MAINTENANCE_DATABASE_URL',os.environ['DATABASE_URL'])
 monkeypatch.setattr('sys.argv',['deletion_control','check'])
 assert deletion_control.main()==0
 assert maintenance.read_state()['state']=='CLOSED'
 assert gated.execute('SELECT count(*) FROM cleanup_jobs WHERE task_id=%s',(task,)).fetchone()==(1,)


@pytest.mark.parametrize('boundary',['ack','complete'])
def test_cleanup_late_close_does_not_commit_ack_or_completion(gated,monkeypatch,boundary):
 from app import cleanup
 from test_cleanup import asset as cleanup_asset,job
 monkeypatch.setenv('S3_ENDPOINT_URL','http://wp07a-synthetic.invalid:9000')
 monkeypatch.setenv('S3_BUCKET','wp07a-synthetic')
 set_gate('OPEN')
 user,task,_=claimed(gated)
 locator='originals/'+str(user)+'/'+str(uuid4())
 aid=cleanup_asset(gated,user,task,'ORIGINAL',locator)
 cleanup.request_delete(task,user)
 job_id=job(gated,task)[0]
 if boundary=='ack':
  def close_after_physical(_asset):set_gate('CLOSED')
  monkeypatch.setattr(cleanup,'_physical',close_after_physical)
 else:
  monkeypatch.setattr(cleanup,'_physical',lambda _asset:None)
  original=cleanup._audit
  def close_after_audit(*args,**kwargs):
   result=original(*args,**kwargs)
   if args[3]=='CLEANUP':set_gate('CLOSED')
   return result
  monkeypatch.setattr(cleanup,'_audit',close_after_audit)
 with pytest.raises(deletion_control.JournalError):cleanup.process_job(job_id)
 state=gated.execute('SELECT cleanup_state,storage_key FROM media_assets WHERE id=%s',(aid,)).fetchone()
 assert state==('REGISTERED' if boundary=='ack' else 'DELETED',locator)
 assert gated.execute('SELECT deleted_at FROM tasks WHERE id=%s',(task,)).fetchone()==(None,)
 assert gated.execute('SELECT status,completed_at FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone()==('PENDING',None)


def test_bootstrap_rejects_identical_decoded_secrets(monkeypatch,tmp_path):
 from app.deployment import configuration
 monkeypatch.setenv('MAINTENANCE_GATE_REQUIRED','true')
 monkeypatch.setenv('WP07_NEW_INSTANCE','true')
 monkeypatch.setenv('MODEL_CALLS_ENABLED','false')
 monkeypatch.setenv('DELETION_JOURNAL_DIR',str(tmp_path))
 for key,role,password in (
  ('INITIAL_DATABASE_URL','init_role','same%2Dsecret'),
  ('MAINTENANCE_DATABASE_URL','maint_role','same-secret'),
  ('RUNTIME_DATABASE_URL','runtime_role','different-secret'),
 ):
  monkeypatch.setenv(key,f'postgresql://{role}:{password}@127.0.0.1:5432/wp07a_test')
 with pytest.raises(ValueError,match='^THREE_DISTINCT_SECRETS_REQUIRED$'):configuration()
 assert list(tmp_path.iterdir())==[]
