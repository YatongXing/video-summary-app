"""Explicit synthetic CR002 regression runner; no .env, business DSN or model calls."""
import argparse,json,os,subprocess,tempfile,shutil,time,socket
from psycopg.conninfo import conninfo_to_dict,make_conninfo
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
PYTHON=ROOT/'backend/.venv/Scripts/python.exe'
def run(args,**kw):
 start=time.monotonic();p=subprocess.run(list(map(str,args)),**kw);print(json.dumps({'exit':p.returncode,'seconds':round(time.monotonic()-start,2)}),flush=True)
 if p.returncode:raise SystemExit(p.returncode)
def main():
 mode=argparse.ArgumentParser();mode.add_argument('mode',choices=['core','linux','010','real']);mode.add_argument('--state');mode.add_argument('--resources',type=Path);a=mode.parse_args()
 resources=json.loads(a.resources.read_text()) if a.resources else None
 if a.mode in ('core','linux'):
  assert resources is not None,'Explicit synthetic --resources JSON required'
  params=conninfo_to_dict(resources['dsn']);assert params['host']=='127.0.0.1' and params['dbname']=='wp04_test' and resources['name'].startswith('vsa-wp04-')
  record=json.loads(subprocess.check_output(['docker','inspect',resources['name']]))[0];assert record['Config']['Labels'].get('vsa.work_package')=='CR-002' and record['Config']['Labels'].get('vsa.synthetic_only')=='true'
 if a.mode=='core':
  name=resources['name'];records=json.loads(subprocess.check_output(['docker','inspect',name]));assert records[0]['Config']['Labels']['vsa.work_package']=='CR-002'
  args=['docker','run','--rm','--label','vsa.work_package=CR-002','--label','vsa.synthetic_only=true','--network','container:'+name,'--user','0','--workdir','/srv','--mount',f'type=bind,source={ROOT/"backend/app"},target=/srv/app,readonly','--mount',f'type=bind,source={ROOT/"backend/tests"},target=/srv/tests,readonly']
  env={'TEST_DATABASE_URL':make_conninfo(**(params|{'port':'5432'})),'WP04_ISOLATED_INSTANCE':name,'WP04_TEST_PORT':'5432','MODEL_CALLS_ENABLED':'false','PYTHONPATH':'/srv','WP04_S3_ENDPOINT':resources['s3_endpoint']}
  for k,v in env.items():args+=['-e',k+'='+v]
  args+=['video-summary-app-test:latest','python','-m','pytest','tests','-q','--tb=short','-p','no:cacheprovider','--ignore=tests/test_cr_support_schema.py','--ignore=tests/test_cr_migrations.py','--ignore=tests/test_cr_roles.py','--ignore=tests/test_cr_cleanup_linux.py','--ignore=tests/test_cr_wp06_integration.py'];run(args)
 elif a.mode=='linux':
  env=os.environ.copy();env.update(WP04_LINUX_DRIVER='true',MODEL_CALLS_ENABLED='false',PYTHONPATH=str(ROOT/'backend'),TEST_DATABASE_URL=resources['dsn'],WP04_ISOLATED_INSTANCE=resources['name'],WP04_TEST_PORT=str(resources['port']));env.pop('DATABASE_URL',None)
  run([PYTHON,'-m','pytest',ROOT/'backend/tests/test_cr_cleanup_linux.py','-q','--tb=short','-p','no:cacheprovider'],env=env)
 elif a.mode=='010':
  # Dedicated 010-only software copy preserves original test guard and SQL assets.
  root=Path(tempfile.mkdtemp(prefix='video-summary-cr002-010-'));name='vsa-wp02-cr002-010'
  try:
   shutil.copytree(ROOT/'backend/app',root/'app',ignore=shutil.ignore_patterns('__pycache__','011_cr_cleanup.sql'));shutil.copytree(ROOT/'backend/tests',root/'tests',ignore=shutil.ignore_patterns('__pycache__'))
   run(['docker','run','-d','--rm','--name',name,'--label','vsa.work_package=CR-002','--label','vsa.synthetic_only=true','--tmpfs','/var/lib/postgresql/data','-e','POSTGRES_PASSWORD=cr002-synthetic','-e','POSTGRES_USER=wp02_bootstrap','-e','POSTGRES_DB=wp02_test','postgres:17-alpine'])
   for _ in range(40):
    if subprocess.run(['docker','exec',name,'pg_isready','-U','wp02_bootstrap','-d','wp02_test'],capture_output=True).returncode==0:break
    time.sleep(.5)
   args=['docker','run','--rm','--label','vsa.work_package=CR-002','--label','vsa.synthetic_only=true','--network','container:'+name,'--workdir','/srv','--mount',f'type=bind,source={root},target=/srv,readonly','-e','TEST_DATABASE_URL=postgresql://wp02_bootstrap:cr002-synthetic@127.0.0.1:5432/wp02_test','-e','WP02_ISOLATED_INSTANCE='+name,'-e','WP02_TEST_PORT=5432','-e','MODEL_CALLS_ENABLED=false','-e','PYTHONPATH=/srv','video-summary-app-test:latest','python','-m','pytest','tests/test_cr_support_schema.py','tests/test_cr_migrations.py','tests/test_cr_roles.py','-q','--tb=short','-p','no:cacheprovider'];run(args)
  finally:
   records=json.loads(subprocess.check_output(['docker','inspect',name]));assert records[0]['Config']['Labels']['vsa.work_package']=='CR-002';subprocess.run(['docker','rm','-f',name],check=True)
   assert root.parent==Path(tempfile.gettempdir()) and root.name.startswith('video-summary-cr002-010-');shutil.rmtree(root)
 else:
  assert a.state;s=json.loads(Path(a.state).read_text());assert s['package']=='CR-WP06' and s['id'].startswith('vsa-wp06-')
  env=os.environ.copy();env.pop('DATABASE_URL',None);env.update(WP06_STATE=a.state,WP06_DIRECT='1',MODEL_CALLS_ENABLED='false',PYTHONPATH=str(ROOT/'backend'))
  run([PYTHON,'-m','pytest',ROOT/'backend/tests/test_cr_wp06_integration.py','-q','--tb=short','-p','no:cacheprovider'],env=env,cwd=ROOT)
if __name__=='__main__':main()
