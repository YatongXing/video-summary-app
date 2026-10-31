"""Repeatable, offline S3-10 HTTP and small-load baseline in isolated resources.

Run inside the existing test image with an explicit *_test database and local S3
credentials. Never loads .env. Output contains aggregate timings, not credentials.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

import boto3
from botocore.config import Config
import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def utc(): return datetime.now(timezone.utc).isoformat()


def measured(samples):
    values=sorted(x['seconds'] for x in samples)
    n=len(values)
    return {'samples':n,'successes':sum(x['ok'] for x in samples),
            'failures':sum(not x['ok'] for x in samples),
            'median_seconds':(values[(n-1)//2]+values[n//2])/2 if n else None,
            'p90_nearest_rank_seconds':values[math.ceil(.9*n)-1] if n else None,
            'raw':samples}


def expect(response,status):
    if response.status_code!=status:
        raise AssertionError(f'Expected HTTP {status}, received {response.status_code}')
    return response.json() if response.content else None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    base=os.environ['TEST_DATABASE_URL']
    if not conninfo_to_dict(base).get('dbname','').endswith('_test'):
        raise ValueError('Only an explicit *_test database is allowed')
    for required in ('S3_ENDPOINT_URL','S3_ACCESS_KEY','S3_SECRET_KEY'):
        if not os.environ.get(required):raise ValueError(f'{required} must be explicitly supplied')
    if not shutil.which('ffmpeg'):raise RuntimeError('Use the existing test image containing FFmpeg')
    identifier='s310_'+uuid4().hex
    schema=identifier;bucket=identifier.replace('_','-')
    scoped=make_conninfo(base,options='-csearch_path='+schema,application_name=identifier)
    store=boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT_URL'],
        aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],
        region_name='us-east-1',config=Config(s3={'addressing_style':'path'},connect_timeout=5,read_timeout=10,retries={'max_attempts':1}))
    report={'started_at':utc(),'status':'RUNNING','model':'offline-fake-v1','external_model_calls':0,
            'environment':{'platform':platform.platform(),'python':platform.python_version(),
                           'container_cpu_count':os.cpu_count()},
            'configuration':{'global_concurrency':1,'import_concurrency':5,'worker_poll_seconds':1,
                             'cleanup_poll_seconds':5,'model_calls_enabled':False},
            'limits':'Synthetic 3-second small media; not a long-video, model-quality, SLO or maximum-capacity claim.'}
    processes=[];clients=[];created_schema=False;created_bucket=False;stop=threading.Event();sampler=None
    resources=[];checks=[];cleanup_errors=[];temporary=None;logs=[]
    try:
        with psycopg.connect(base,autocommit=True) as control:
            control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)));created_schema=True
        from app.migrate import run_migrations
        with psycopg.connect(scoped,autocommit=True) as db:run_migrations(db)
        store.create_bucket(Bucket=bucket);created_bucket=True
        temporary=tempfile.TemporaryDirectory(prefix=identifier+'-')
        work=Path(temporary.name)
        env={key:value for key,value in os.environ.items() if key not in ('DASHSCOPE_API_KEY','DASHSCOPE_BASE_URL','QWEN_MODEL')}
        env.update(DATABASE_URL=scoped,S3_BUCKET=bucket,MODEL_CALLS_ENABLED='false',
            WORKER_PROCESSOR_MODE='offline',QUEUE_GLOBAL_CONCURRENCY='1',IMPORT_CONCURRENCY='5',
            WORKER_POLL_SECONDS='1',CLEANUP_ENABLED='true',CLEANUP_POLL_SECONDS='5',
            IMPORT_STAGING_DIR=str(work/'staging'),PROCESSING_WORK_DIR=str(work/'processing'),
            DELETION_JOURNAL_DIR=str(work/'journal'),BACKUP_INVENTORY_CONFIRMED='false',
            AUTH_ALLOWED_ORIGINS='http://127.0.0.1:18080',AUTH_COOKIE_SECURE='false')
        media=work/'synthetic.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc2=size=320x180:rate=10:duration=3',
            '-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(media)],check=True,timeout=30)
        payload=media.read_bytes();report['media']={'duration_seconds':3,'bytes':len(payload),'container':'MP4','video_codec':'H.264','audio_codec':'AAC'}
        logs=[]
        for name,command in [('api',[sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','18080','--no-proxy-headers']),
                             ('worker',[sys.executable,'-m','app.worker'])]:
            log=(work/(name+'.log')).open('w+');logs.append(log)
            processes.append(subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT))
        def new_client():
            client=httpx.Client(base_url='http://127.0.0.1:18080',timeout=45,trust_env=False,headers={'Origin':'http://127.0.0.1:18080'})
            clients.append(client);return client
        probe=new_client();deadline=time.monotonic()+30
        while True:
            try:
                if probe.get('/health/ready').status_code==200:break
            except httpx.TransportError:pass
            if time.monotonic()>deadline:raise RuntimeError('Isolated API did not become ready')
            if any(p.poll() is not None for p in processes):raise RuntimeError('Isolated application process exited')
            time.sleep(.1)
        expect(probe.get('/tasks'),401);checks.append('anonymous tasks denied')
        password='Synthetic-baseline-only-310!'
        first=expect(probe.post('/auth/register',json={'email':'baseline0@example.invalid','password':password}),201)
        from app.auth import password_hash
        hashed=password_hash(password)
        with psycopg.connect(scoped,autocommit=True) as db:
            for number in range(1,30):db.execute('INSERT INTO users(email_normalized,password_hash) VALUES (%s,%s)',(f'baseline{number}@example.invalid',hashed))
            db.execute("INSERT INTO users(email_normalized,password_hash,role) VALUES ('baseline-admin@example.invalid',%s,'ADMIN')",(hashed,))
        users=[]
        for number in range(30):
            client=new_client();login=expect(client.post('/auth/login',json={'email':f'baseline{number}@example.invalid','password':password}),200)
            client.headers['X-CSRF-Token']=login['csrf_token'];users.append(client)
        admin=new_client();login=expect(admin.post('/auth/login',json={'email':'baseline-admin@example.invalid','password':password}),200);admin.headers['X-CSRF-Token']=login['csrf_token']
        checks.append('registration plus 30 persistent-account logins and deployer-seeded admin login')
        report['stage']='input_rejections'
        expect(users[0].post('/inputs/upload',content=payload,headers={'X-Video-Filename':'synthetic.mp4'}),400)
        checks.append('missing rights rejected')
        rejected=users[0].post('/inputs/upload',content=b'not a video',headers={'X-Video-Filename':'synthetic.mp4','X-Upload-Authorized':'true'})
        if rejected.status_code not in (400,415,422):raise AssertionError(f'Corrupt media was not rejected: HTTP {rejected.status_code}')
        if expect(users[0].get('/tasks'),200):raise AssertionError('Input rejection created a task')
        if expect(users[0].get('/quota'),200)['credits']!=3:raise AssertionError('Input rejection charged a credit')
        checks.append('corrupt media rejected; both rejections created no task or charge')
        def sample():
            try:
                with psycopg.connect(scoped,autocommit=True) as db:
                    while not stop.is_set():
                        row={'at':utc(),'monotonic':time.monotonic()}
                        for label,query in [('active_imports',"SELECT count(*) FROM input_imports WHERE state IN ('STAGING','STORING')"),
                            ('processing',"SELECT count(*) FROM tasks WHERE status='PROCESSING'"),('queued',"SELECT count(*) FROM tasks WHERE status='QUEUED'")]:
                            row[label]=db.execute(query).fetchone()[0]
                        row['pg_lock_waiters']=db.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'",(identifier,)).fetchone()[0]
                        for key,file in [('cgroup_memory_bytes','/sys/fs/cgroup/memory.current'),('cgroup_memory_limit','/sys/fs/cgroup/memory.max')]:
                            try:row[key]=Path(file).read_text().strip()
                            except OSError:row[key]=None
                        try:row['cgroup_cpu_stat']=Path('/sys/fs/cgroup/cpu.stat').read_text().strip()
                        except OSError:row['cgroup_cpu_stat']=None
                        try:row['cgroup_io_stat']=Path('/sys/fs/cgroup/io.stat').read_text().strip()
                        except OSError:row['cgroup_io_stat']=None
                        resources.append(row);stop.wait(.2)
            except Exception as error:report['resource_sampling_error']=type(error).__name__
        sampler=threading.Thread(target=sample,daemon=True);sampler.start()
        report['stage']='five_simultaneous_uploads'
        barrier=threading.Barrier(5)
        def upload(number):
            barrier.wait(timeout=10);started=time.monotonic()
            response=users[number].post('/inputs/upload',content=payload,headers={'X-Video-Filename':'synthetic.mp4','X-Upload-Authorized':'true'})
            item={'seconds':time.monotonic()-started,'ok':response.status_code==201,'status':response.status_code}
            return item,response.json().get('id') if response.status_code==201 else None
        with ThreadPoolExecutor(max_workers=5) as pool:uploaded=list(pool.map(upload,range(5)))
        report['five_simultaneous_uploads']=measured([x[0] for x in uploaded])
        task_ids=[x[1] for x in uploaded if x[1]]
        if len(task_ids)!=5:raise AssertionError('One or more of the five uploads failed')
        report['stage']='authenticated_read_load'
        report['authenticated_read_load']=[]
        for count in (10,30):
            read_barrier=threading.Barrier(count)
            def read(number):
                read_barrier.wait(timeout=15);samples=[]
                for _ in range(3):
                    for path in ('/tasks','/quota'):
                        started=time.monotonic();response=users[number].get(path)
                        samples.append({'seconds':time.monotonic()-started,'ok':response.status_code==200,'status':response.status_code,'operation':path})
                return samples
            started=time.monotonic()
            with ThreadPoolExecutor(max_workers=count) as pool:batches=list(pool.map(read,range(count)))
            stats=measured([item for batch in batches for item in batch]);stats.update(users=count,iterations_per_user=3,wall_seconds=time.monotonic()-started)
            report['authenticated_read_load'].append(stats)
            if stats['failures']:raise AssertionError('Authenticated read load failed')
        report['stage']='task_completion'
        deadline=time.monotonic()+120
        while True:
            states=[expect(users[i].get('/tasks/'+tid),200) for i,tid in enumerate(task_ids)]
            if all(x['status'] in ('SUCCEEDED','FAILED','CANCELLED') for x in states):break
            if time.monotonic()>deadline:raise RuntimeError('Offline tasks did not settle')
            time.sleep(.2)
        if any(x['status']!='SUCCEEDED' for x in states):raise AssertionError('Offline task failed')
        report['task_timings']=[{key:row.get(key) for key in ('status','submitted_at','started_at','finished_at','queue_seconds','processing_seconds','duration_seconds')} for row in states]
        success_times=measured([{'ok':True,'seconds':row['processing_seconds']} for row in states if row['status']=='SUCCEEDED'])
        success_times['scope']='Successful synthetic 3-second videos only; actual start to finish, queue excluded; not the 60-minute target.'
        report['successful_processing_duration']=success_times
        report['queue_duration']=measured([{'ok':True,'seconds':row['queue_seconds']} for row in states])
        report['stage']='results_and_content_authorization'
        for language in ('zh','en'):
            document=expect(users[0].get(f'/tasks/{task_ids[0]}/export?language={language}'),200)
            from app.results import validate_export
            validate_export(document)
        expect(users[1].get('/tasks/'+task_ids[0]+'/export'),404)
        expect(admin.get('/admin/tasks/'+task_ids[0]+'/result?purpose=diagnosis'),403)
        consent=expect(users[0].post('/tasks/'+task_ids[0]+'/consents',json={'purpose':'diagnosis','access_scope':'summary'}),201)
        expect(admin.get('/admin/tasks/'+task_ids[0]+'/result?purpose=diagnosis'),200)
        expect(admin.get('/admin/tasks/'+task_ids[0]+'/video?purpose=diagnosis'),403)
        expect(users[0].delete('/tasks/'+task_ids[0]+'/consents/'+consent['id']),204)
        expect(admin.get('/admin/tasks/'+task_ids[0]+'/result?purpose=diagnosis'),403)
        checks.extend(['offline independent Worker and bilingual Schema1.0 exports','cross-user denial','task-purpose-scope consent and revocation'])
        report['stage']='deletion'
        for i,tid in enumerate(task_ids):
            expect(users[i].delete('/tasks/'+tid),202)
            expect(users[i].get('/tasks/'+tid+'/export'),404)
        deadline=time.monotonic()+30
        while True:
            receipts=[expect(users[i].get('/deletions'),200) for i in range(5)]
            if all(any(x['task_id']==task_ids[i] and x['online_complete'] for x in rows) for i,rows in enumerate(receipts)):break
            if time.monotonic()>deadline:raise RuntimeError('Online deletion did not finish')
            time.sleep(.2)
        with psycopg.connect(scoped,autocommit=True) as db:
            if db.execute('SELECT count(*) FROM task_results').fetchone()[0]:raise AssertionError('Result bodies remain after deletion')
            if db.execute("SELECT count(*) FROM media_assets WHERE cleanup_state<>'DELETED'").fetchone()[0]:raise AssertionError('Assets remain unverified')
        if store.list_objects_v2(Bucket=bucket).get('KeyCount',0):raise AssertionError('Private objects remain after deletion')
        checks.append('all five originals/results removed, durable deletion receipts, backup still unverified')
        report['stage']='completed'
        report['status']='PASSED'
    except Exception as error:
        report['status']='FAILED';report['error_type']=type(error).__name__
        # No raw provider, HTTP body, credential or environment dump.
        report['error_message']=str(error) if isinstance(error,(AssertionError,RuntimeError,ValueError)) else 'See exception class; raw details intentionally omitted'
    finally:
        stop.set()
        if sampler:sampler.join(timeout=3)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=8)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
        for client in clients:client.close()
        for log in logs:log.close()
        if temporary is not None:
            try:temporary.cleanup()
            except Exception as error:cleanup_errors.append('temporary: '+type(error).__name__)
        if created_bucket:
            try:
                for page in store.get_paginator('list_objects_v2').paginate(Bucket=bucket):
                    for obj in page.get('Contents',[]):store.delete_object(Bucket=bucket,Key=obj['Key'])
                store.delete_bucket(Bucket=bucket)
            except Exception as error:cleanup_errors.append('bucket: '+type(error).__name__)
        if created_schema:
            try:
                with psycopg.connect(base,autocommit=True) as control:control.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
            except Exception as error:cleanup_errors.append('schema: '+type(error).__name__)
        report.update(finished_at=utc(),checks=checks,resources=resources,cleanup_errors=cleanup_errors)
        report['resource_peaks']={key:max((row.get(key,0) for row in resources),default=None) for key in ('active_imports','processing','queued','pg_lock_waiters')}
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'status':report['status'],'report':str(args.output),'checks':len(checks),'cleanup_errors':cleanup_errors}))
    return 0 if report['status']=='PASSED' and not cleanup_errors else 1

if __name__=='__main__':raise SystemExit(main())
