"""WP07-A dedicated synthetic environment. No existing resources or .env used."""
import argparse,json,os,socket,subprocess,tempfile,time
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2]
LABELS=['--label','vsa.work_package=CR-WP07-A','--label','vsa.synthetic_only=true']
def docker(*args,check=True,code=None):
 p=subprocess.run(['docker',*map(str,args)],input=code,capture_output=True,text=True,timeout=180)
 if check and p.returncode:raise RuntimeError('Dedicated operation failed: '+p.stderr[-1500:])
 return p.stdout.strip()
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def save(s):Path(s['state']).write_text(json.dumps(s,indent=2),encoding='utf-8')
def load(path):
 s=json.loads(Path(path).read_text());assert s['package']=='CR-WP07-A' and s['synthetic_only'] is True and s['id'].startswith('vsa-wp07a-test-')
 names=[s['network'],*s['volumes'],*container_names(s)]
 assert all(n.startswith(s['id']+'-') for n in names)
 return s
def checked(name,kind='container'):
 d=json.loads(docker(kind,'inspect',name))[0];labels=d['Config']['Labels'] if kind=='container' else d['Labels'];assert labels.get('vsa.work_package')=='CR-WP07-A' and labels.get('vsa.synthetic_only')=='true';return d
def run(s,name,image,env={},mounts=[],ports=[],command=[],user=None):
 assert name.startswith(s['id']+'-')
 assert not docker('ps','-aq','--filter','name=^/'+name+'$')
 a=['run','-d','--name',name,*LABELS,'--network',s['network']]
 if user:a+=['--user',user]
 for k,v in env.items():a+=['-e',k+'='+str(v)]
 for m in mounts:a+=['--mount',m]
 for x,y in ports:a+=['-p',f'127.0.0.1:{x}:{y}']
 return docker(*a,image,*command)
def execute(s,code,maint=False):
 a=['run','--rm',*LABELS,'--network',s['network'],'--user','10001','--mount',f'type=bind,source={ROOT/"backend/app"},target=/srv/app,readonly','--mount',f'type=volume,source={s["volumes"][2]},target=/control/journal','--mount',f'type=volume,source={s["volumes"][3]},target=/control/staging','--mount',f'type=volume,source={s["volumes"][4]},target=/control/processing']
 env=s['service_env'].copy()
 if maint:env.update(DATABASE_URL=s['maint_dsn'],MAINTENANCE_DATABASE_URL=s['maint_dsn'])
 for k,v in env.items():a+=['-e',k+'='+str(v)]
 return docker(*a,s['image'],'python','-c',code)
def state(s,value):return execute(s,"from app.maintenance import set_state;print(set_state("+repr(value)+"))",True)
def start():
 ident='vsa-wp07a-test-'+uuid4().hex[:10];root=Path(tempfile.mkdtemp(prefix='video-summary-wp07a-'));dbp,apip,s3p=[port() for _ in range(3)]
 s={'id':ident,'package':'CR-WP07-A','synthetic_only':True,'root':str(root),'state':str(root/'state.json'),'image':'video-summary-app-wp07a-test:latest','network':ident+'-net','volumes':[ident+'-pg',ident+'-s3',ident+'-control',ident+'-staging',ident+'-processing'],'containers':{'db':ident+'-db','s3':ident+'-s3','api':ident+'-api','workers':[ident+'-worker1',ident+'-worker2']},'api_url':f'http://127.0.0.1:{apip}','api_path_prefix':'','port':dbp,'s3_endpoint':f'http://127.0.0.1:{s3p}','bucket':'wp07a-synthetic','phase':'starting'}
 for role in ['init','maint','runtime']:s[role+'_dsn']=f'postgresql://wp07_{role}:WP07-synthetic-{role}-only@{s["containers"]["db"]}:5432/wp07a_test'
 s['host_maint_dsn']=s['maint_dsn'].replace(s['containers']['db']+':5432',f'127.0.0.1:{dbp}')
 env={'DATABASE_URL':s['runtime_dsn'],'DELETION_JOURNAL_DIR':'/control/journal','PROCESSING_WORK_DIR':'/control/processing','IMPORT_STAGING_DIR':'/control/staging','MODEL_CALLS_ENABLED':'false','MAINTENANCE_GATE_REQUIRED':'true','WORKER_PROCESSOR_MODE':'offline','QUEUE_GLOBAL_CONCURRENCY':'2','CLEANUP_ENABLED':'true','CLEANUP_POLL_SECONDS':'2','WORKER_LEASE_SECONDS':'30','WORKER_HEARTBEAT_SECONDS':'1','AUTH_COOKIE_SECURE':'false','AUTH_ALLOWED_ORIGINS':s['api_url'],'S3_ENDPOINT_URL':f'http://{s["containers"]["s3"]}:9000','S3_ACCESS_KEY':'wp07synthetic','S3_SECRET_KEY':'wp07-synthetic-only','S3_BUCKET':s['bucket'],'PYTHONPATH':'/srv'}
 s['service_env']=env;save(s)
 docker('network','create',*LABELS,s['network'])
 for v in s['volumes']:docker('volume','create',*LABELS,v)
 run(s,s['containers']['db'],'postgres:17-alpine',{'POSTGRES_USER':'wp07_init','POSTGRES_PASSWORD':'WP07-synthetic-init-only','POSTGRES_DB':'wp07a_test'},[f'type=volume,source={s["volumes"][0]},target=/var/lib/postgresql/data'],[(dbp,5432)])
 for _ in range(90):
  if 'accepting connections' in docker('exec',s['containers']['db'],'pg_isready','-U','wp07_init','-d','wp07a_test',check=False):break
  time.sleep(.5)
 a=['run','--rm',*LABELS,'--user','0','--mount',f'type=volume,source={s["volumes"][2]},target=/control/journal','--mount',f'type=volume,source={s["volumes"][3]},target=/control/staging','--mount',f'type=volume,source={s["volumes"][4]},target=/control/processing','--mount',f'type=volume,source={s["volumes"][1]},target=/data',s['image'],'python','-c',"from pathlib import Path;import os;[Path('/control/'+p).mkdir(parents=True,exist_ok=True) for p in ['journal','staging','processing']];os.system('chown -R 10001:10001 /control /data')"]
 docker(*a)
 run(s,s['containers']['s3'],'rustfs/rustfs:1.0.4',{'RUSTFS_ACCESS_KEY':'wp07synthetic','RUSTFS_SECRET_KEY':'wp07-synthetic-only','RUSTFS_VOLUMES':'/data'},[f'type=volume,source={s["volumes"][1]},target=/data'],[(s3p,9000)],user='10001')
 from urllib.request import build_opener,ProxyHandler
 opener=build_opener(ProxyHandler({}))
 for _ in range(90):
  try:
   with opener.open(s['s3_endpoint']+'/health',timeout=2) as response:
    if response.status==200:break
  except OSError:time.sleep(.5)
 else:raise RuntimeError('Dedicated S3 readiness timeout')
 init_env=env|{'INITIAL_DATABASE_URL':s['init_dsn'],'MAINTENANCE_DATABASE_URL':s['maint_dsn'],'RUNTIME_DATABASE_URL':s['runtime_dsn'],'WP07_NEW_INSTANCE':'true'}
 a=['run','--rm',*LABELS,'--network',s['network'],'--user','10001','--mount',f'type=bind,source={ROOT/"backend/app"},target=/srv/app,readonly','--mount',f'type=volume,source={s["volumes"][2]},target=/control/journal','--mount',f'type=volume,source={s["volumes"][3]},target=/control/staging','--mount',f'type=volume,source={s["volumes"][4]},target=/control/processing']
 for k,v in init_env.items():a+=['-e',k+'='+v]
 docker(*a,s['image'],'python','-m','app.deployment','initialize')
 execute(s,"from app import auth\nwith auth.connect() as c:\n for email,role in [('user1@wp07a.invalid','USER'),('user2@wp07a.invalid','USER'),('admin@wp07a.invalid','ADMIN')]:\n  c.execute('INSERT INTO users(email_normalized,password_hash,role) VALUES(%s,%s,%s)',(email,auth.password_hash('WP07-synthetic-only!'),role))",True)
 mounts=[f'type=bind,source={ROOT/"backend/app"},target=/srv/app,readonly',f'type=volume,source={s["volumes"][2]},target=/control/journal',f'type=volume,source={s["volumes"][3]},target=/control/staging',f'type=volume,source={s["volumes"][4]},target=/control/processing']
 run(s,s['containers']['api'],s['image'],env,mounts,[(apip,8000)],['python','-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000'],user='10001')
 for w in s['containers']['workers']:run(s,w,s['image'],env,mounts,command=['python','-m','app.worker'],user='10001')
 s['phase']='running';s['accounts']=[{'email':e,'role':role,'password':'WP07-synthetic-only!'} for e,role in [('user1@wp07a.invalid','USER'),('user2@wp07a.invalid','USER'),('admin@wp07a.invalid','ADMIN')]]
 s['samples']={}
 for label,duration in [('short',3),('long',30)]:
  output='/control/processing/'+label+'.mp4';execute(s,"import subprocess;subprocess.run("+repr(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'testsrc2=size=320x180:rate=12:duration={duration}','-f','lavfi','-i',f'sine=frequency=440:duration={duration}','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart','-shortest',output])+",check=True)")
  target=root/(label+'.mp4');docker('cp',s['containers']['api']+':'+output,str(target));s['samples'][label]=str(target)
 save(s);print(json.dumps({'state':s['state'],'api_url':s['api_url']}));return s

def web(s):
 webport=port();s['url']=f'http://127.0.0.1:{webport}';s['containers']['web']=s['id']+'-web'
 s['service_env']['AUTH_ALLOWED_ORIGINS']=s['api_url']+','+s['url'];s['service_env']['SUPPORT_EMAIL']='feedback@example.invalid'
 api=s['containers']['api'];checked(api);docker('rm','-f',api)
 mounts=[f'type=bind,source={ROOT/"backend/app"},target=/srv/app,readonly']+[f'type=volume,source={s["volumes"][i]},target=/control/{d}' for i,d in [(2,'journal'),(3,'staging'),(4,'processing')]]
 run(s,api,s['image'],s['service_env'],mounts,[(int(s['api_url'].rsplit(':',1)[1]),8000)],['python','-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000'],user='10001')
 run(s,s['containers']['web'],'video-summary-app-wp07a-web-test:latest',{'WP07_API_UPSTREAM':'http://'+api+':8000'},ports=[(webport,8080)],user='10001')
 save(s);return s['url']

def container_names(s):
 c=s['containers']
 return ([c['web']] if c.get('web') else [])+[*c['workers'],c['api'],c['s3'],c['db']]

def stop(s):
 names=container_names(s)
 assert len(names)==len(set(names)) and len(s['volumes'])==len(set(s['volumes']))
 for v in s['volumes']:checked(v,'volume')
 network=checked(s['network'],'network')
 assert {v['Name'] for v in (network.get('Containers') or {}).values()} <= set(names)
 found=[]
 for name in names:
  if docker('ps','-aq','--filter','name=^/'+name+'$'):
   container=checked(name)
   for mount in container['Mounts']:
    if mount['Type']=='bind':
     assert mount['Destination']=='/srv/app' and not mount['RW']
    else:
     assert mount['Type']=='volume' and mount['Name'] in s['volumes']
   found.append(name)
 for name in found:docker('rm','-f',name)
 for v in s['volumes']:docker('volume','rm',v)
 docker('network','rm',s['network'])
 s['phase']='stopped';save(s)
def main():
 a=argparse.ArgumentParser();a.add_argument('action',choices=['start','stop','open','close']);a.add_argument('--state');p=a.parse_args()
 if p.action=='start':start()
 else:
  s=load(p.state)
  if p.action=='stop':stop(s)
  else:print(state(s,p.action.upper()))
if __name__=='__main__':main()
