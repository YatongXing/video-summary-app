"""WP07-A actual HTTP/PG/private store/nonroot gate and monitoring tests."""
import json,os,time,subprocess,sys
from datetime import datetime,timezone
from uuid import uuid4
from pathlib import Path
import pytest,httpx,psycopg
ROOT=Path(__file__).parents[2]
sys.path.insert(0,str(ROOT/'backend/tools'))
import wp07a_environment as lab
S=lab.load(os.environ['WP07A_STATE']) if os.getenv('WP07A_STATE') else None
pytestmark=pytest.mark.skipif(S is None,reason='Dedicated WP07-A state required')
def wait(fn,seconds=60):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  try:
   if v:=fn():return v
  except (httpx.RequestError,psycopg.OperationalError):pass
  time.sleep(.25)
 raise AssertionError('Dedicated condition timeout')
def sql(text,args=()):
 with psycopg.connect(S['host_maint_dsn'],autocommit=True) as c:return c.execute(text,args).fetchall()
@pytest.fixture(scope='module')
def clients():
 lab.state(S,'OPEN');cs=[]
 for a in S['accounts']:
  c=httpx.Client(base_url=S['api_url'],timeout=60,trust_env=False);r=c.post('/auth/login',json={'email':a['email'],'password':a['password']});assert r.status_code==200,r.text;c.headers['X-CSRF-Token']=r.json()['csrf_token'];cs.append(c)
 yield cs
 for c in cs:c.close()
def upload(c):
 r=c.post('/inputs/upload',content=Path(S['samples']['long']).read_bytes(),headers={'X-Upload-Authorized':'true','X-Video-Filename':'wp07a-synthetic.mp4'});assert r.status_code==201,r.text;t=r.json()['id'];wait(lambda:any(x['id']==t and x['status']=='SUCCEEDED' for x in c.get('/tasks').json()));return t

def test_new_roles_migrations_and_nonroot(clients):
 rows=sql('SELECT version,sha256 FROM schema_migrations ORDER BY version');assert len(rows)==11
 import hashlib
 assert {a:b for a,b in rows}=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'backend/app/migrations').glob('*.sql')}
 roles=sql("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname IN('wp07_maint','wp07_runtime')")
 assert len(roles)==2 and all(not any(row[1:]) for row in roles)
 for n in [S['containers']['api'],*S['containers']['workers']]:
  lab.checked(n);assert lab.docker('exec',n,'id','-u')=='10001'
  env=json.loads(lab.docker('inspect','--format','{{json .Config.Env}}',n));assert not any(x.startswith(('MAINTENANCE_DATABASE_URL=','INITIAL_DATABASE_URL=')) for x in env)
 assert sql("SELECT has_table_privilege('wp07_runtime','support_requests','INSERT'),has_column_privilege('wp07_runtime','support_requests','scope','SELECT'),has_schema_privilege('wp07_runtime','public','CREATE'),pg_has_role('wp07_runtime','wp07_maint','MEMBER')")==[(False,False,False,False)]

def test_owner_full_pipeline_and_metadata(clients):
 global TASKS
 TASKS=[upload(c) for c in clients]
 for c,t in zip(clients,TASKS):
  for language in ['zh','en']:
   assert c.get(f'/tasks/{t}/result?language={language}').status_code==200
   assert c.get(f'/tasks/{t}/export?language={language}').json()['language']==language
  v=c.get(f'/tasks/{t}/video',headers={'Range':'bytes=2-31'});assert v.status_code==206 and len(v.content)==30
 assert clients[2].get(f'/tasks/{TASKS[0]}/result').status_code==404
 assert clients[0].get(f'/tasks/{TASKS[1]}/video').status_code==404
 m=clients[2].get('/admin/tasks',params={'task_id':TASKS[0][:1]});assert m.status_code==200
 assert all(not any(k in row for k in ['summary','presentation','storage_key','export_document']) for row in m.json())

def test_persistent_closed_across_api_worker_restart(clients):
 lab.state(S,'CLOSED')
 try:
  for path in ['/tasks','/quota','/admin/tasks','/health/ready',f'/tasks/{TASKS[0]}/video']:
   assert clients[0].get(path).status_code==503
  for n in [S['containers']['api'],S['containers']['workers'][0]]:lab.checked(n);lab.docker('restart',n)
  wait(lambda:httpx.get(S['api_url']+'/health/live',trust_env=False).status_code==200)
  assert clients[0].get('/tasks').status_code==503
 finally:lab.state(S,'OPEN')
 wait(lambda:clients[0].get('/health/ready').status_code==200)
 assert clients[0].get(f'/tasks/{TASKS[0]}/result').status_code==200

def monitor(events,repeat=0):
 import wp07_monitor
 return wp07_monitor.observe(S,events,repeat_seconds=repeat)

def records(events):
 try:return [json.loads(line) for line in events.read_text(encoding='utf-8').splitlines()]
 except (FileNotFoundError,json.JSONDecodeError):return []

def assert_local_records(events):
 data=records(events)
 assert data
 assert all(record['channel']=='LOCAL_TEST_FILE' and record['formal_recipient_confirmed'] is False and record['id']==S['id'] for record in data)
 text=json.dumps(data)
 assert all(word not in text for word in ['WP07-synthetic','postgresql://','summary','password','wp07synthetic'])
 return data

def test_monitor_real_outages_and_recovery(clients,tmp_path):
 events=tmp_path/'notifications.jsonl'
 wait(lambda:monitor(events)['healthy'])
 for component,reason in [('db','DATABASE_STOPPED'),('s3','STORAGE_STOPPED'),('api','API_STOPPED')]:
  n=S['containers'][component];lab.checked(n);lab.docker('stop',n)
  try:
   assert reason in monitor(events)['faults']
   assert reason in monitor(events)['faults']
   assert records(events)[-1]['event']=='CONTINUING'
  finally:lab.docker('start',n)
  wait(lambda:monitor(events)['healthy'])
  assert records(events)[-1]['event']=='RECOVERED'
 n=S['containers']['workers'][0];lab.checked(n);lab.docker('pause',n)
 try:assert 'WORKER_PAUSED' in monitor(events)['faults']
 finally:lab.docker('unpause',n)
 wait(lambda:monitor(events)['healthy'])
 assert assert_local_records(events)[-1]['event']=='RECOVERED'

def test_periodic_windows_monitor_detects_worker_and_continuing_fault(clients,tmp_path):
 events=tmp_path/'periodic-notifications.jsonl'
 history=events.with_suffix(events.suffix+'.state.json')
 proc=subprocess.Popen(
  [sys.executable,str(ROOT/'backend/tools/wp07_monitor.py'),'--state',S['state'],'--events',str(events),'--interval','2','--repeat-seconds','3'],
  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),
 )
 def healthy_observation():
  assert proc.poll() is None
  try:return json.loads(history.read_text(encoding='utf-8'))['faults']==[]
  except (FileNotFoundError,json.JSONDecodeError):return False
 def event(kind,fault=None):
  assert proc.poll() is None
  return next((r for r in records(events) if r['event']==kind and (fault is None or fault in r['faults'])),None)
 worker=S['containers']['workers'][0]
 try:
  wait(healthy_observation)
  lab.checked(worker)
  lab.docker('stop',worker)
  try:
   first=wait(lambda:event('FAULT','WORKER_STOPPED'))
   continuing=wait(lambda:event('CONTINUING','WORKER_STOPPED'))
   assert (datetime.fromisoformat(continuing['at_utc'])-datetime.fromisoformat(first['at_utc'])).total_seconds()>=3
  finally:lab.docker('start',worker)
  wait(lambda:event('RECOVERED'))
  assert_local_records(events)
 finally:
  if proc.poll() is None:proc.terminate()
  proc.wait(timeout=10)

def test_monitor_persistent_cleanup_state_and_recovery(clients,tmp_path):
 events=tmp_path/'cleanup-notifications.jsonl'
 wait(lambda:monitor(events)['healthy'])
 user=sql("SELECT id FROM users WHERE role='USER' LIMIT 1")[0][0]
 task=sql("INSERT INTO tasks(user_id,input_kind,status,deletion_requested_at) VALUES(%s,'LOCAL','CANCELLED',now()) RETURNING id",(user,))[0][0]
 try:
  sql("INSERT INTO cleanup_jobs(task_id,user_id,status,blocked_reason,started_at) VALUES(%s,%s,'BLOCKED','CONFIG',now()) RETURNING id",(task,user))
  result=monitor(events)
  assert 'PERSISTENT_CLEANUP_FAULT' in result['faults'] and 'CLEANUP_UNHEALTHY' in result['faults']
  assert records(events)[-1]['event']=='FAULT'
  monitor(events)
  assert records(events)[-1]['event']=='CONTINUING'
 finally:
  sql('DELETE FROM cleanup_jobs WHERE task_id=%s RETURNING id',(task,))
  sql('DELETE FROM tasks WHERE id=%s RETURNING id',(task,))
 wait(lambda:monitor(events)['healthy'])
 assert assert_local_records(events)[-1]['event']=='RECOVERED'

def test_monitor_probe_budget_failure_delivers_and_recovers(tmp_path,monkeypatch):
 import wp07_monitor
 events=tmp_path/'deadline-notifications.jsonl'
 original=wp07_monitor.subprocess.run
 def timeout(args,**kwargs):
  assert 0<kwargs['timeout']<=.25
  raise subprocess.TimeoutExpired(args,kwargs['timeout'])
 with monkeypatch.context() as context:
  context.setattr(wp07_monitor,'PROBE_BUDGET_SECONDS',.25)
  context.setattr(wp07_monitor.subprocess,'run',timeout)
  assert monitor(events)['faults']==['PROBE_DEADLINE']
  assert monitor(events)['faults']==['PROBE_DEADLINE']
 assert wp07_monitor.subprocess.run is original
 wait(lambda:monitor(events)['healthy'])
 assert [r['event'] for r in assert_local_records(events)]==['FAULT','CONTINUING','RECOVERED']

def test_delete_physical_and_other_task_preserved(clients):
 t=TASKS[0];r=clients[0].delete(f'/tasks/{t}');assert r.status_code==202 and r.json()['accepted']
 assert clients[0].get(f'/tasks/{t}/result').status_code==404
 wait(lambda:sql('SELECT status FROM cleanup_jobs WHERE task_id=%s',(t,))==[('COMPLETED',)])
 assert sql('SELECT count(*) FROM task_results WHERE task_id=%s',(t,))==[(0,)]
 assert sql("SELECT count(*) FROM media_assets WHERE task_id=%s AND cleanup_state<>'DELETED'",(t,))==[(0,)]
 assert clients[2].get(f'/tasks/{TASKS[2]}/result').status_code==200

def test_runtime_permissions_retired_paths_and_physical_cleanup(clients):
 from psycopg import errors
 from boto3 import client as storage_client
 from botocore.config import Config
 from botocore.exceptions import ClientError
 user,_,admin=clients
 host_runtime=S['runtime_dsn'].replace(S['containers']['db']+':5432',f"127.0.0.1:{S['port']}")
 with psycopg.connect(host_runtime,autocommit=True) as connection:
  assert connection.execute("SELECT current_user,pg_get_userbyid(relowner) FROM pg_class WHERE oid='tasks'::regclass").fetchone()==('wp07_runtime','wp07_maint')
  for query in (
   'CREATE TABLE public.forbidden(id int)',
   'ALTER TABLE public.tasks ADD COLUMN forbidden int',
   'SET ROLE wp07_maint',
   'SELECT scope FROM support_requests LIMIT 1',
   'INSERT INTO support_requests DEFAULT VALUES',
   'SELECT access_scope FROM task_consents LIMIT 1',
   'INSERT INTO task_consents DEFAULT VALUES',
  ):
   with pytest.raises(errors.InsufficientPrivilege):connection.execute(query)
 task=upload(user)
 uid=sql('SELECT user_id FROM tasks WHERE id=%s',(task,))[0][0]
 req=sql("WITH stamp AS (SELECT clock_timestamp() AS t) INSERT INTO support_requests(task_id,user_id,scope,created_at,expires_at) SELECT %s,%s,'both',t,t+interval '1 day' FROM stamp RETURNING id",(task,uid))[0][0]
 sql("INSERT INTO task_consents(task_id,user_id,purpose,access_scope) VALUES(%s,%s,'synthetic','video') RETURNING id",(task,uid))
 for suffix in ('result','video','export'):
  assert admin.get(f'/tasks/{task}/{suffix}').status_code==404
  assert admin.get(f'/admin/tasks/{task}/{suffix}').status_code==410
  assert admin.get(f'/admin/support-requests/{req}/{suffix}').status_code==410
 assert user.post(f'/tasks/{task}/support-requests',json={'scope':'both'}).status_code==410
 assert admin.get('/admin/support-requests').status_code==410
 assert user.get('/admin/tasks').status_code==403
 response=admin.get('/admin/tasks',params={'task_id':task.upper()})
 assert response.status_code==200 and len(response.json())==1
 assert set(response.json()[0])=={
  'id','user_id','status','created_at','submitted_at','started_at','finished_at',
  'failure_category','failure_code','retry_count','credits_available','queue_seconds',
  'processing_seconds','quota_unlimited','retry_records','quota_events','model_calls',
 }
 assert admin.get('/admin/tasks',params={'task_id':'%_does-not-match'}).json()==[]
 assert any(row['id']==task for row in admin.get('/admin/tasks',params={'task_id':''}).json())
 bucket,key=sql("SELECT storage_bucket,storage_key FROM media_assets WHERE task_id=%s AND asset_kind='ORIGINAL'",(task,))[0]
 settings=S['service_env']
 store=storage_client('s3',endpoint_url=S['s3_endpoint'],aws_access_key_id=settings['S3_ACCESS_KEY'],aws_secret_access_key=settings['S3_SECRET_KEY'],region_name='us-east-1',config=Config(proxies={},s3={'addressing_style':'path'}))
 try:
  assert store.head_object(Bucket=bucket,Key=key)['ContentLength']>0
  assert user.delete(f'/tasks/{task}').status_code==202
  wait(lambda:sql('SELECT status FROM cleanup_jobs WHERE task_id=%s',(task,))==[('COMPLETED',)])
  assert sql('SELECT count(*) FROM support_requests WHERE id=%s',(req,))==[(0,)]
  assert sql('SELECT count(*) FROM task_consents WHERE task_id=%s',(task,))==[(0,)]
  with pytest.raises(ClientError) as error:store.head_object(Bucket=bucket,Key=key)
  assert error.value.response['ResponseMetadata']['HTTPStatusCode']==404
  assert user.get(f'/tasks/{task}/video').status_code==404
 finally:store.close()


def test_strict_offline_replay_owner_only(clients):
 from datetime import timedelta
 api=S['containers']['api']
 lab.checked(api)
 uid=sql("SELECT id FROM users WHERE role='USER' LIMIT 1")[0][0]
 task=sql("INSERT INTO tasks(user_id,input_kind) VALUES(%s,'LOCAL') RETURNING id",(uid,))[0][0]
 lab.state(S,'CLOSED')
 try:
  lab.execute(S,"from app import deletion_control,tasks;deletion_control.record_intent("+repr(str(task))+","+repr(str(uid))+",tasks.utcnow())",True)
  stamp=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
  for command in (['check'],['restore','--snapshot-created-at',stamp]):
   for supplied in (False,True):
    code="import os,sys;from app.deletion_control import main;sys.argv="+repr(['deletion_control',*command])+";"
    if supplied:code+="os.environ['MAINTENANCE_DATABASE_URL']=os.environ['DATABASE_URL'];"
    code+="raise SystemExit(main())"
    result=subprocess.run(['docker','exec',api,'python','-c',code],capture_output=True,text=True,timeout=15)
    assert result.returncode!=0
    assert sql('SELECT deletion_requested_at FROM tasks WHERE id=%s',(task,))==[(None,)]
  assert 'replayed tasks: 1' in lab.execute(S,"import sys;from app.deletion_control import main;sys.argv=['deletion_control','check'];raise SystemExit(main())",True)
  assert sql('SELECT deletion_requested_at IS NOT NULL FROM tasks WHERE id=%s',(task,))==[(True,)]
  restore="import sys;from app.deletion_control import main;sys.argv="+repr(['deletion_control','restore','--snapshot-created-at',stamp])+";raise SystemExit(main())"
  assert 'replayed tasks:' in lab.execute(S,restore,True)
  assert clients[0].get('/tasks').status_code==503
 finally:lab.state(S,'OPEN')
