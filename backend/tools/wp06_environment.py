"""WP06 synthetic-only local environment. Never reads .env or operates business resources."""
from __future__ import annotations
import argparse, functools, http.client, http.server, json, os, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import build_opener,ProxyHandler
from uuid import uuid4

REPO=Path(__file__).resolve().parents[2]
IMAGE='video-summary-app-test:latest'
LABELS=['--label','vsa.work_package=CR-WP06','--label','vsa.synthetic_only=true']

def docker(*args,check=True):
    p=subprocess.run(['docker',*map(str,args)],capture_output=True,text=True,timeout=120)
    if check and p.returncode:raise RuntimeError('Dedicated Docker operation failed: '+p.stderr[-900:])
    return p.stdout.strip()

def freeport():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def save(path,state):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2),encoding='utf-8');os.replace(tmp,path)

def load(path):
    s=json.loads(path.read_text(encoding='utf-8'))
    if s.get('package')!='CR-WP06' or not s['id'].startswith('vsa-wp06-'):raise RuntimeError('Unrecognized test state')
    names=[s['containers']['db'],s['containers']['s3'],s['containers']['api'],*s['containers']['workers'],s['network'],*s['volumes']]
    if any(not name.startswith(s['id']+'-') for name in names):raise RuntimeError('Cross-instance resource in state')
    return s

def checked(name,kind='container'):
    raw=docker(kind,'inspect',name)
    record=json.loads(raw)[0]
    labels=record['Config']['Labels'] if kind=='container' else record.get('Labels',{})
    if labels.get('vsa.work_package')!='CR-WP06' or labels.get('vsa.synthetic_only')!='true':raise RuntimeError('Resource ownership mismatch')
    return record

def run_container(name,image,*args,env=None,mounts=(),ports=(),network=None,command=()):
    cmd=['run','-d','--name',name,*LABELS]
    if network:cmd+=['--network',network]
    for a,b in ports:cmd+=['-p',f'127.0.0.1:{a}:{b}']
    for m in mounts:cmd+=['--mount',m]
    for k,v in (env or {}).items():cmd+=['-e',f'{k}={v}']
    cmd += list(args)+[image]+list(command)
    docker(*cmd)

def wait_pg(name):
    for _ in range(90):
        if 'accepting connections' in docker('exec',name,'pg_isready','-U','wp06_maint','-d','wp06_test',check=False):return
        time.sleep(1)
    raise RuntimeError('Test PG readiness timeout')

def exec_python(s,code,maintenance=False):
    env=s['service_env'].copy()
    if maintenance:env['DATABASE_URL']=s['internal_db_url']
    cmd=['run','--rm',*LABELS,'--network',s['network'],'--user','0','--mount',f'type=bind,source={REPO/"backend"/"app"},target=/srv/app,readonly','--mount',f'type=volume,source={s["volumes"][2]},target=/control']
    for k,v in env.items():cmd+=['-e',k+'='+str(v)]
    return docker(*cmd,IMAGE,'python','-c',code)

def seed(s):
    code="""import json
from app import auth
users=[]
with auth.connect() as c:
 for email,role in [('user1@wp06.invalid','USER'),('user2@wp06.invalid','USER'),('admin@wp06.invalid','ADMIN')]:
  row=c.execute("INSERT INTO users(email_normalized,password_hash,role) VALUES(%s,%s,%s) ON CONFLICT(email_normalized) DO UPDATE SET role=EXCLUDED.role RETURNING id",(email,auth.password_hash('WP06-synthetic-only!'),role)).fetchone()
  users.append({'id':str(row['id']),'email':email,'password':'WP06-synthetic-only!','role':role})
print(json.dumps(users))
"""
    s['accounts']=json.loads(exec_python(s,code,True))

def start(path=None):
    if path and Path(path).exists():raise RuntimeError('State exists; use status or stop, never overwrite')
    token=uuid4().hex[:10];identifier='vsa-wp06-'+token
    root=Path(tempfile.mkdtemp(prefix='video-summary-wp06-'))
    (root/'.wp06-owner').write_text(identifier,encoding='utf-8')
    path=Path(path) if path else root/'state.json'
    if path.exists():raise RuntimeError('State exists; use status or stop, never overwrite')
    dbp,s3p,apip,uip=[freeport() for _ in range(4)]
    password='WP06-local-synthetic'
    containers={'db':identifier+'-db','s3':identifier+'-s3','api':identifier+'-api','workers':[identifier+'-worker1',identifier+'-worker2']}
    s={'package':'CR-WP06','id':identifier,'root':str(root),'state_path':str(path.resolve()),'network':identifier+'-net','volumes':[identifier+'-pg',identifier+'-s3data',identifier+'-control'],'containers':containers,
       'url':f'http://127.0.0.1:{uip}','api_url':f'http://127.0.0.1:{apip}',
       'db_url':f'postgresql://wp06_maint:{password}@127.0.0.1:{dbp}/wp06_test','runtime_db_url':f'postgresql://wp06_runtime:{password}@127.0.0.1:{dbp}/wp06_test',
       'internal_db_url':f'postgresql://wp06_maint:{password}@{containers["db"]}:5432/wp06_test','internal_runtime_db_url':f'postgresql://wp06_runtime:{password}@{containers["db"]}:5432/wp06_test',
       's3_endpoint':f'http://127.0.0.1:{s3p}','s3_internal_endpoint':f'http://{containers["s3"]}:9000','s3_access_key':'wp06synthetic','s3_secret_key':'wp06-synthetic-secret','bucket':'wp06-synthetic','phase':'starting'}
    env={'DATABASE_URL':s['internal_runtime_db_url'],'S3_ENDPOINT_URL':s['s3_internal_endpoint'],'S3_ACCESS_KEY':s['s3_access_key'],'S3_SECRET_KEY':s['s3_secret_key'],'S3_BUCKET':s['bucket'],
         'DELETION_JOURNAL_DIR':'/control/journal','PROCESSING_WORK_DIR':'/control/processing','IMPORT_STAGING_DIR':'/control/staging','MODEL_CALLS_ENABLED':'false','WORKER_PROCESSOR_MODE':'offline','QUEUE_GLOBAL_CONCURRENCY':'2','CLEANUP_ENABLED':'true','CLEANUP_POLL_SECONDS':'5','WORKER_HEARTBEAT_SECONDS':'1','WORKER_LEASE_SECONDS':'30','AUTH_COOKIE_SECURE':'false','AUTH_ALLOWED_ORIGINS':s['url']+','+s['api_url'],'PYTHONPATH':'/srv','SUPPORT_EMAIL':os.environ.get('SUPPORT_EMAIL','')}
    s['service_env']=env;save(path,s)
    try:
        docker('network','create',*LABELS,s['network'])
        for v in s['volumes']:docker('volume','create',*LABELS,v)
        run_container(containers['db'],'postgres:17-alpine',env={'POSTGRES_USER':'wp06_maint','POSTGRES_PASSWORD':password,'POSTGRES_DB':'wp06_test'},mounts=[f'type=volume,source={s["volumes"][0]},target=/var/lib/postgresql/data'],ports=[(dbp,5432)],network=s['network'])
        wait_pg(containers['db'])
        run_container(containers['s3'],'rustfs/rustfs:1.0.4',env={'RUSTFS_ACCESS_KEY':s['s3_access_key'],'RUSTFS_SECRET_KEY':s['s3_secret_key'],'RUSTFS_VOLUMES':'/data'},mounts=[f'type=volume,source={s["volumes"][1]},target=/data'],ports=[(s3p,9000)],network=s['network'])
        bootstrap="""from app.migrate import run_migrations
import os,psycopg
with psycopg.connect(os.environ['DATABASE_URL']) as c:
 run_migrations(c)
 c.execute("CREATE ROLE wp06_runtime LOGIN PASSWORD 'WP06-local-synthetic' NOSUPERUSER NOCREATEDB NOCREATEROLE")
 c.execute("GRANT CONNECT ON DATABASE wp06_test TO wp06_runtime")
 c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
 c.execute("GRANT USAGE ON SCHEMA public TO wp06_runtime")
 c.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO wp06_runtime")
 c.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO wp06_runtime")
 c.execute("REVOKE ALL ON task_consents FROM wp06_runtime")
 c.execute("GRANT DELETE,SELECT(task_id,user_id) ON task_consents TO wp06_runtime")
 c.execute("REVOKE ALL ON schema_migrations FROM wp06_runtime")
"""
        exec_python(s,bootstrap,True)
        exec_python(s,"from app.deletion_control import initialize;initialize();import json;from pathlib import Path;p=Path('/control/journal');(p/'runtime').mkdir(exist_ok=True);(p/'runtime'/'producer-ready').write_text((p/'identity.json').read_text())")
        for _ in range(60):
            try:exec_python(s,"from app import object_store;s=object_store.client();s.create_bucket(Bucket=object_store.bucket());print('ready')");break
            except RuntimeError:time.sleep(1)
        else:raise RuntimeError('Private synthetic storage readiness timeout')
        seed(s)
        mounts=[f'type=bind,source={REPO/"backend"/"app"},target=/srv/app,readonly',f'type=volume,source={s["volumes"][2]},target=/control']
        run_container(containers['api'],IMAGE,'--user','0',env=env,mounts=mounts,ports=[(apip,8000)],network=s['network'],command=['python','-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000','--no-proxy-headers'])
        for name in containers['workers']:run_container(name,IMAGE,'--user','0',env=env,mounts=mounts,network=s['network'],command=['python','-m','app.worker'])
        # Generate only local, rights-free lavfi media, never model output.
        samples={}
        for label,duration in [('short',3),('long',30)]:
            name='/control/'+label+'.mp4'
            cmd="import subprocess;subprocess.run("+repr(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i',f'testsrc2=size=320x180:rate=12:duration={duration}','-f','lavfi','-i',f'sine=frequency=440:duration={duration}','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart','-shortest',name])+",check=True)"
            exec_python(s,cmd)
            target=root/(label+'.mp4');docker('cp',containers['api']+':'+name,str(target));samples[label]=str(target)
        s['samples']=samples;save(path,s)
        # Explicit clean environment, no user credentials/model keys inherited.
        safe={k:v for k,v in os.environ.items() if k in ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP','HOME','USERPROFILE')}
        log=(root/'proxy.log').open('ab')
        proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'serve','--state',str(path.resolve())],env=safe,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        log.close();s['proxy_pid']=proc.pid;save(path,s)
        opener=build_opener(ProxyHandler({}))
        for _ in range(30):
            if proc.poll() is not None:raise RuntimeError('Local frontend proxy exited')
            try:
                with opener.open(s['url']+'/api/health/live',timeout=2) as response:
                    if response.status==200:break
            except OSError:time.sleep(.5)
        else:raise RuntimeError('Local frontend/API readiness timeout')
        s['phase']='running';save(path,s)
        print(json.dumps({'state':str(path.resolve()),'url':s['url'],'accounts':s['accounts'],'samples':samples}))
        return s
    except BaseException:
        s['phase']='failed';save(path,s);raise

class Proxy(http.server.SimpleHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_GET(self):self.forward() if self.path.startswith('/api/') else super().do_GET()
    def do_HEAD(self):self.forward() if self.path.startswith('/api/') else super().do_HEAD()
    def do_POST(self):self.forward()
    def do_DELETE(self):self.forward()
    def forward(self):
        if not self.path.startswith('/api/'):self.send_error(404);return
        target=urlsplit(self.server.api_url);con=http.client.HTTPConnection(target.hostname,target.port,timeout=60)
        try:
            length=int(self.headers.get('Content-Length','0'))
            chunked=self.headers.get('Transfer-Encoding','').lower()=='chunked'
            headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','transfer-encoding')}
            con.putrequest(self.command,self.path[4:],skip_accept_encoding=True)
            for k,v in headers.items():con.putheader(k,v)
            if chunked:con.putheader('Transfer-Encoding','chunked')
            con.endheaders()
            if chunked:
                while True:
                    line=self.rfile.readline(8193)
                    if len(line)>8192 or not line.endswith(b'\r\n'):raise ConnectionError('Invalid chunk header')
                    size=int(line.split(b';',1)[0].strip(),16)
                    if size<0:raise ConnectionError('Invalid chunk length')
                    con.send(line)
                    if not size:
                        while True:
                            trailer=self.rfile.readline(8193)
                            if len(trailer)>8192 or not trailer.endswith(b'\r\n'):raise ConnectionError('Invalid trailer')
                            con.send(trailer)
                            if trailer==b'\r\n':break
                        break
                    left=size
                    while left:
                        data=self.rfile.read(min(65536,left))
                        if not data:raise ConnectionError('Interrupted chunk')
                        con.send(data);left-=len(data)
                    ending=self.rfile.read(2)
                    if ending!=b'\r\n':raise ConnectionError('Invalid chunk ending')
                    con.send(ending)
            remaining=0 if chunked else length
            while remaining:
                chunk=self.rfile.read(min(65536,remaining))
                if not chunk:break
                con.send(chunk);remaining-=len(chunk)
            response=con.getresponse();self.send_response(response.status)
            for k,v in response.getheaders():
                if k.lower() not in ('connection','transfer-encoding'):self.send_header(k,v)
            self.send_header('Connection','close');self.end_headers()
            if self.command!='HEAD':
                while True:
                    chunk=response.read1(65536)
                    if not chunk:break
                    self.wfile.write(chunk);self.wfile.flush()
        except (OSError,ValueError,http.client.HTTPException):
            self.close_connection=True
        finally:con.close();self.close_connection=True

def serve(path):
    s=load(path)
    handler=functools.partial(Proxy,directory=str(REPO/'frontend'/'dist'))
    server=http.server.ThreadingHTTPServer(('127.0.0.1',urlsplit(s['url']).port),handler);server.api_url=s['api_url']
    server.serve_forever()

def status(path):
    s=load(path);results={}
    for name in [s['containers']['db'],s['containers']['s3'],s['containers']['api'],*s['containers']['workers']]:
        try:results[name]=checked(name)['State']['Status']
        except Exception:results[name]='missing-or-untrusted'
    out={'state':str(path),'url':s['url'],'phase':s['phase'],'containers':results,'accounts':s.get('accounts',[]),'samples':s.get('samples',{})}
    print(json.dumps(out));return out

def stop(path):
    s=load(path)
    # Dedicated helper is identified by its state-bound command line, not PID alone.
    pid=s.get('proxy_pid')
    if pid:
        if os.name=='nt':
            script="$p=Get-CimInstance Win32_Process -Filter \"ProcessId = "+str(int(pid))+"\"; if($p -and $p.CommandLine -like '*wp06_environment.py*' -and $p.CommandLine -like '*"+str(path.resolve()).replace("'","''")+"*'){Stop-Process -Id "+str(int(pid))+" -Force}"
            subprocess.run(['powershell','-NoProfile','-Command',script],capture_output=True,timeout=20)
        else:
            try:
                args=Path('/proc/'+str(pid)+'/cmdline').read_bytes()
                if b'wp06_environment.py' in args and os.fsencode(str(path.resolve())) in args:os.kill(pid,15)
            except FileNotFoundError:pass
    for name in [*s['containers']['workers'],s['containers']['api'],s['containers']['s3'],s['containers']['db']]:
        if not docker('ps','-a','--filter','name=^/'+name+'$','--format','{{.Names}}'):continue
        checked(name);docker('rm','-f',name)
    for v in s['volumes']:
        if docker('volume','ls','--filter','name=^'+v+'$','--format','{{.Name}}'):checked(v,'volume');docker('volume','rm',v)
    if docker('network','ls','--filter','name=^'+s['network']+'$','--format','{{.Name}}'):checked(s['network'],'network');docker('network','rm',s['network'])
    root=Path(s['root']).resolve()
    if root.exists() and s.get('phase')!='stopped':
        if root.parent!=Path(tempfile.gettempdir()).resolve() or not root.name.startswith('video-summary-wp06-') or (root/'.wp06-owner').read_text(encoding='utf-8')!=s['id']:
            raise RuntimeError('Temporary directory ownership mismatch; not removed')
        shutil.rmtree(root)
    s['phase']='stopped';save(path,s);print(json.dumps({'stopped':True,'state':str(path),'temporary_media_removed':True}))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['start','serve','status','stop','seed']);p.add_argument('--state',type=Path)
    args=p.parse_args()
    if args.command=='start':start(args.state);return
    if not args.state:p.error('--state required')
    if args.command=='serve':serve(args.state)
    elif args.command=='status':status(args.state)
    elif args.command=='stop':stop(args.state)
    else:s=load(args.state);seed(s);save(args.state,s);print(json.dumps(s['accounts']))
if __name__=='__main__':main()
