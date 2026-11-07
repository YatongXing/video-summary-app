"""Host pytest driver: two actual Linux containers, local shared volume, real FFmpeg."""
import json,os,subprocess,time
from pathlib import Path
from uuid import uuid4
import pytest
from cr_wp04_fixtures import wp04_db

def docker(*args,check=True):
 r=subprocess.run(['docker',*args],text=True,capture_output=True,timeout=60)
 if check and r.returncode:raise AssertionError(r.stderr[-1500:])
 return r

@pytest.fixture
def linux_pair():
 if os.environ.get('WP04_LINUX_DRIVER')!='true':pytest.skip('Host Docker driver requires explicit WP04_LINUX_DRIVER=true')
 token=uuid4().hex[:10];modern=bool(os.environ.get('WP07A_ISOLATED_INSTANCE'));prefix='vsa-wp07a-test-' if modern else 'vsa-wp04-';package='CR-WP07-A' if modern else 'CR-WP04';volume=prefix+'lock-'+token
 names=[prefix+'producer-'+token,prefix+'cleaner-'+token]
 docker('volume','create','--label','vsa.work_package='+package,'--label','vsa.synthetic_only=true',volume)
 if modern:
  assert os.environ['WP07A_ISOLATED_INSTANCE'].startswith('vsa-wp07a-test-')
  docker('run','--rm','--label','vsa.work_package='+package,'--label','vsa.synthetic_only=true','--user','0','--mount','type=volume,source='+volume+',target=/control','video-summary-app-test:latest','python','-c','import os;os.chown("/control",10001,10001)')
 app=str(Path(__file__).parents[1]/'app')
 try:
  for name in names:
   docker('run','-d','--rm','--name',name,'--label','vsa.work_package='+package,'--label','vsa.synthetic_only=true','--user','10001' if modern else '0',
    '--mount','type=volume,source='+volume+',target=/control',
    '--mount','type=bind,source='+app+',target=/srv/app,readonly',
    '-e','DELETION_JOURNAL_DIR=/control','-e','PYTHONPATH=/srv','-e','MODEL_CALLS_ENABLED=false',
    'video-summary-app-test:latest','python','-c','import time;time.sleep(180)')
  yield names
 finally:
  for name in names:docker('rm','-f',name,check=False)
  docker('volume','rm',volume)

def execute(name,code):
 return docker('exec',name,'python','-c',code)

@pytest.mark.parametrize('tool',['ffmpeg','ffprobe'])
def test_parent_sigkill_media_child_keeps_shared_lock_until_child_exits(linux_pair,tool):
 producer,cleaner=linux_pair
 task=str(uuid4())
 code="""import subprocess,os,time,json,signal
from pathlib import Path
from app.task_locks import producer,inherited_fds
with producer(TASK):
 if TOOL=='ffprobe':
  output=open('/control/frames.raw','wb')
  p=subprocess.Popen(['ffprobe','-v','error','-f','lavfi','-i','testsrc=size=64x64:rate=10,realtime','-show_frames','-of','compact'],stdout=output,pass_fds=inherited_fds())
 else:
  p=subprocess.Popen(['ffmpeg','-nostdin','-v','error','-re','-f','lavfi','-i','testsrc=size=64x64:rate=10','-t','90','-pix_fmt','rgb24','-f','rawvideo','/control/frames.raw'],pass_fds=inherited_fds())
 while not Path('/control/frames.raw').exists() or Path('/control/frames.raw').stat().st_size==0:
  if p.poll() is not None:raise RuntimeError('Real FFmpeg exited before writing')
  time.sleep(.02)
 Path('/control/child.pid').write_text(str(p.pid))
 os.kill(os.getpid(),signal.SIGKILL)
""".replace('TASK',repr(task)).replace('TOOL',repr(tool))
 docker('exec','-d',producer,'python','-c',code)
 deadline=time.monotonic()+20
 while time.monotonic()<deadline:
  if execute(producer,"from pathlib import Path;print(Path('/control/child.pid').exists())").stdout.strip()=='True':break
  time.sleep(.05)
 else:raise AssertionError('Real child never reached producing state')
 first=int(execute(cleaner,"from pathlib import Path;print(Path('/control/frames.raw').stat().st_size)").stdout)
 deadline=time.monotonic()+5
 later=first
 while later<=first and time.monotonic()<deadline:
  time.sleep(.2)
  later=int(execute(cleaner,"from pathlib import Path;print(Path('/control/frames.raw').stat().st_size)").stdout)
 assert later>first,'Child must continue actual writes after parent SIGKILL'
 check="""from app.task_locks import exclusive,LockBusy
try:
 with exclusive(TASK):raise AssertionError('EX incorrectly acquired while orphan FFmpeg writes')
except LockBusy:print('busy')
""".replace('TASK',repr(task))
 assert execute(cleaner,check).stdout.strip()=='busy'
 execute(producer,"import os,signal;from pathlib import Path;os.kill(int(Path('/control/child.pid').read_text()),signal.SIGKILL)")
 execute(cleaner,"from app.task_locks import exclusive\nwith exclusive("+repr(task)+"): print('exclusive-after-child-stop')")

def test_independent_tasks_are_not_globally_serialized(linux_pair):
 a,b=linux_pair;t1=str(uuid4());t2=str(uuid4())
 # Hold two different producer scopes simultaneously in different containers.
 for name,task,marker in [(a,t1,'a'),(b,t2,'b')]:
  code="import time;from pathlib import Path;from app.task_locks import producer\nwith producer("+repr(task)+"):\n Path('/control/"+marker+"').write_text('held');time.sleep(15)"
  docker('exec','-d',name,'python','-c',code)
 for marker in ('a','b'):
  deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   if execute(a,"from pathlib import Path;print(Path('/control/"+marker+"').exists())").stdout.strip()=='True':break
   time.sleep(.02)
  else:raise AssertionError('Independent producer scope serialized task '+marker)
 code="from app.task_locks import exclusive,LockBusy\ntry:\n with exclusive("+repr(t1)+"):raise AssertionError('active task lock escaped')\nexcept LockBusy:print('busy')"
 assert execute(b,code).stdout.strip()=='busy'

def test_global_io_mutex_and_inode_replacement(linux_pair):
 a,b=linux_pair
 docker('exec','-d',a,'python','-c',"import time;from pathlib import Path;from app.task_locks import cleanup_global\nwith cleanup_global():\n Path('/control/global-held').write_text('held');time.sleep(15)")
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  if execute(b,"from pathlib import Path;print(Path('/control/global-held').exists())").stdout.strip()=='True':break
  time.sleep(.02)
 else:raise AssertionError('Global physical lock not entered')
 assert execute(b,"from app.task_locks import cleanup_global,LockBusy\ntry:\n with cleanup_global():raise AssertionError('Second physical I/O entered')\nexcept LockBusy:print('busy')").stdout.strip()=='busy'
 task=str(uuid4())
 execute(b,"from app.task_locks import producer\nwith producer("+repr(task)+"):pass")
 # Locate only this synthetic task lock in dedicated volume; never host files.
 code="""from pathlib import Path
from app.task_locks import exclusive,LockUnsafe
items=[p for p in Path('/control/locks').iterdir() if TASK in p.name and not p.name.endswith('.anchor')]
assert len(items)==1,[p.name for p in items]
p=items[0];p.unlink();p.write_bytes(b'')
try:
 with exclusive(TASK):raise AssertionError('Replaced lock inode silently accepted')
except LockUnsafe:print('unsafe')
""".replace('TASK',repr(task))
 assert execute(b,code).stdout.strip()=='unsafe'

def test_real_workers_parallel_delete_a_keeps_b(wp04_db,linux_pair):
 import psycopg
 from psycopg.conninfo import conninfo_to_dict,make_conninfo
 from cr_wp04_fixtures import queued,row
 from app import cleanup
 db=wp04_db;a,b=linux_pair
 u1,t1=queued(db);u2,t2=queued(db)
 # Docker connects only the already-created WP04 dedicated PG via host loopback publication.
 params=conninfo_to_dict(os.environ['DATABASE_URL'])
 params['host']='host.docker.internal';params['port']=os.environ['WP04_TEST_PORT']
 dsn=make_conninfo(**params)
 deployment=str(db.execute('SELECT deployment_id FROM deletion_control_state').fetchone()[0])
 execute(a,"import json;from pathlib import Path;p=Path('/control/runtime');p.mkdir(exist_ok=True);Path('/control/identity.json').write_text(json.dumps({'deployment_id':"+repr(deployment)+"}));(p/'producer-ready').write_text(json.dumps({'deployment_id':"+repr(deployment)+"}))")
 code="""import os,threading
from pathlib import Path
from app.worker import run
from app import processing
from app.preprocess import _run
def processor(ctx):
 with processing._workspace(ctx) as path:
  Path('/control/running-'+str(ctx.id)).write_text(str(path))
  _run(['ffmpeg','-nostdin','-v','error','-re','-f','lavfi','-i','testsrc=size=64x64:rate=10','-t','90','-pix_fmt','rgb24','-f','rawvideo',str(path/'evidence.raw')],checkpoint=ctx.checkpoint,check=True)
run(threading.Event(),.1,processor=processor,heartbeat_seconds=1)
"""
 for name in (a,b):
  docker('exec','-d','-e','DATABASE_URL='+dsn,'-e','PROCESSING_WORK_DIR=/control/work','-e','QUEUE_GLOBAL_CONCURRENCY=2','-e','WORKER_LEASE_SECONDS=120',name,'python','-c',code)
 deadline=time.monotonic()+15
 while time.monotonic()<deadline:
  running=db.execute("SELECT count(*) FROM tasks WHERE status='PROCESSING'").fetchone()[0]
  if running==2:
   info=execute(a,"import json;from pathlib import Path;print(json.dumps({p.name:p.read_text() for p in Path('/control').glob('running-*')}))").stdout
   paths=json.loads(info)
   if len(paths)==2:break
  time.sleep(.1)
 else:raise AssertionError('Two real Workers never concurrently produced distinct users tasks')
 assert db.execute("SELECT count(*) FROM users WHERE credits_available=2").fetchone()[0]==2
 bpath=paths['running-'+str(t2)]+'/evidence.raw'
 deadline=time.monotonic()+10
 while time.monotonic()<deadline:
  size=int(execute(b,"from pathlib import Path;p=Path("+repr(bpath)+");print(p.stat().st_size if p.exists() else 0)").stdout)
  if size:break
  time.sleep(.1)
 else:raise AssertionError('B never produced actual media bytes')
 cleanup.request_delete(t1,u1);jid=row(db,t1)['id']
 deadline=time.monotonic()+10
 while time.monotonic()<deadline:
  out=docker('exec','-e','DATABASE_URL='+dsn,'-e','PROCESSING_WORK_DIR=/control/work',a,'python','-c',"from app.cleanup import process_job;print(process_job("+repr(str(jid))+")['status'])").stdout
  if out.strip()=='COMPLETED':break
  time.sleep(.1)
 else:raise AssertionError('A did not stop/release SH and safely clean')
 assert db.execute("SELECT status FROM tasks WHERE id=%s",(t2,)).fetchone()[0]=='PROCESSING'
 assert execute(a,"from pathlib import Path;print(Path("+repr(paths['running-'+str(t1)])+").exists())").stdout.strip()=='False'
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  later=int(execute(b,"from pathlib import Path;print(Path("+repr(bpath)+").stat().st_size)").stdout)
  if later>size:break
  time.sleep(.2)
 assert later>size,'Deletion of A stopped or removed B media producer'

def test_db_disconnect_does_not_release_active_physical_mutex(wp04_db,linux_pair):
 from psycopg.conninfo import conninfo_to_dict,make_conninfo
 db=wp04_db;a,b=linux_pair
 params=conninfo_to_dict(os.environ['DATABASE_URL']);params['host']='host.docker.internal';params['port']=os.environ['WP04_TEST_PORT']
 dsn=make_conninfo(**params)
 code="""import time,psycopg
from pathlib import Path
from app.task_locks import cleanup_global
c=psycopg.connect(DSN)
pid=c.execute('SELECT pg_backend_pid()').fetchone()[0];c.commit()
with cleanup_global():
 Path('/control/io-pid').write_text(str(pid))
 while not Path('/control/io-release').exists():
  with Path('/control/active-io.bin').open('ab') as f:f.write(b'synthetic-io');f.flush()
  try:c.execute('SELECT 1');c.commit()
  except psycopg.Error:Path('/control/db-disconnected').write_text('yes')
  time.sleep(.05)
c.close()
""".replace('DSN',repr(dsn))
 docker('exec','-d',a,'python','-c',code)
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  if execute(b,"from pathlib import Path;print(Path('/control/io-pid').exists())").stdout.strip()=='True':break
  time.sleep(.05)
 else:raise AssertionError('Physical mutex holder failed to start')
 pid=int(execute(b,"from pathlib import Path;print(Path('/control/io-pid').read_text())").stdout)
 assert db.execute('SELECT pg_terminate_backend(%s)',(pid,)).fetchone()==(True,)
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  if execute(b,"from pathlib import Path;print(Path('/control/db-disconnected').exists())").stdout.strip()=='True':break
  time.sleep(.05)
 else:raise AssertionError('Target synthetic DB connection did not fail')
 first=int(execute(b,"from pathlib import Path;print(Path('/control/active-io.bin').stat().st_size)").stdout)
 time.sleep(.2)
 later=int(execute(b,"from pathlib import Path;print(Path('/control/active-io.bin').stat().st_size)").stdout)
 assert later>first
 assert execute(b,"from app.task_locks import cleanup_global,LockBusy\ntry:\n with cleanup_global():raise AssertionError('DB loss caused unsafe physical takeover')\nexcept LockBusy:print('busy')").stdout.strip()=='busy'
 execute(a,"from pathlib import Path;Path('/control/io-release').write_text('release')")
 deadline=time.monotonic()+5
 while time.monotonic()<deadline:
  r=docker('exec',b,'python','-c',"from app.task_locks import cleanup_global\nwith cleanup_global():print('released')",check=False)
  if r.returncode==0:break
  time.sleep(.05)
 assert r.returncode==0
