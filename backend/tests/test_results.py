"""Independent S3-08 output-contract, isolation and atomic publication checks."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

from fastapi import HTTPException
import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest


def candidate(language='zh', events=True):
    return {'language':language, 'summary':'离线替身摘要。' if language=='zh' else 'Offline synthetic summary.',
            'key_events':[{'timestamp_seconds':1.5,'title':'测试事件','description':'Test event.'}] if events else [],
            'overview':'离线测试总览' if language=='zh' else 'Offline test overview',
            'points':['First point','Second point','Third point'],
            'chapters':[{'timestamp_seconds':0,'title':'Beginning'}],
            'limitations':['Synthetic content only; insufficient events for three key moments.']}


def validate(value, language='zh'):
    from app.results import validate_output
    return validate_output(value,'example.mp4',60,language)


@pytest.mark.parametrize('language',['zh','en'])
def test_schema_exact_export_and_separate_presentation(language):
    output=validate(candidate(language),language)
    document=output['export']
    assert set(document)=={'schema_version','video','language','summary','key_events'}
    assert document['schema_version']=='1.0' and document['language']==language
    assert document['video']=={'filename':'example.mp4','duration_seconds':60}
    assert set(document['key_events'][0])=={'timestamp_seconds','title','description'}
    assert output['presentation']['overview'] and len(output['presentation']['points'])==3
    assert json.loads(json.dumps(document,allow_nan=False))==document


def test_empty_events_are_valid_when_summary_and_limitations_exist():
    assert validate(candidate(events=False))['export']['key_events']==[]


@pytest.mark.parametrize('mutate',[
    lambda c:c.update(language='fr'),
    lambda c:c.update(summary='  '),
    lambda c:c.update(summary=None),
    lambda c:c.update(user_id='private-id'),
    lambda c:c.update(transcript='Full transcript must not be exported'),
    lambda c:c['key_events'][0].update(importance=1),
    lambda c:c['key_events'][0].update(timestamp_seconds=True),
    lambda c:c['key_events'][0].update(timestamp_seconds=float('nan')),
    lambda c:c['key_events'][0].update(timestamp_seconds=float('inf')),
    lambda c:c['key_events'][0].update(timestamp_seconds=-1),
    lambda c:c['key_events'][0].update(timestamp_seconds=60.01),
    lambda c:c['key_events'][0].update(timestamp_seconds='1.5'),
    lambda c:c['key_events'][0].update(description=''),
    lambda c:c['key_events'].append({'timestamp_seconds':0,'title':'Earlier','description':'Unsorted'}),
    lambda c:c.update(points=['Only one']),
    lambda c:c.update(chapters=[{'timestamp_seconds':61,'title':'Out of bounds'}]),
])
def test_invalid_content_cannot_become_export(mutate):
    from app.results import OutputError
    value=candidate();mutate(value)
    with pytest.raises(OutputError):validate(value)


@pytest.fixture
def result_db(monkeypatch):
    base=os.getenv('TEST_DATABASE_URL')
    if not base:pytest.skip('Explicit isolated *_test database required')
    if not conninfo_to_dict(base).get('dbname','').endswith('_test'):pytest.fail('Only *_test database allowed')
    schema='results_test_'+uuid4().hex
    with psycopg.connect(base,autocommit=True) as control:
        control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        scoped=make_conninfo(base,options='-csearch_path='+schema)
        monkeypatch.setenv('DATABASE_URL',scoped)
        monkeypatch.setenv('QUEUE_GLOBAL_CONCURRENCY','4')
        monkeypatch.setenv('WORKER_LEASE_SECONDS','3600')
        try:
            from app.migrate import run_migrations
            with psycopg.connect(scoped,autocommit=True) as db:
                run_migrations(db)
                yield db
        finally:control.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def identity(db,role='USER'):
    return db.execute('INSERT INTO users(email_normalized,password_hash,role) VALUES (%s,%s,%s) RETURNING id',
                      (str(uuid4())+'@example.invalid','synthetic-only',role)).fetchone()[0]


def claimed(db,uid=None):
    from app import queue
    uid=uid or identity(db)
    tid=db.execute("INSERT INTO tasks(user_id,input_kind,display_filename,duration_seconds) VALUES (%s,'LOCAL','example.mp4',60) RETURNING id",(uid,)).fetchone()[0]
    claim=queue.claim_next('result-test-worker')
    assert claim['id']==str(tid)
    return uid,tid,claim['generation']


def publish(tid,gen,**kwargs):
    from app import results
    return results.publish(tid,gen,'result-test-worker',{'zh':candidate(),'en':candidate('en')},synthetic=True,**kwargs)


def test_publish_both_languages_and_success_are_atomic(result_db):
    db=result_db;uid,tid,gen=claimed(db)
    publish(tid,gen)
    assert db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='SUCCEEDED'
    assert db.execute('SELECT status FROM task_attempts WHERE task_id=%s',(tid,)).fetchone()[0]=='SUCCEEDED'
    assert db.execute('SELECT count(*) FROM task_results WHERE task_id=%s',(tid,)).fetchone()[0]==2
    assert db.execute('SELECT count(*) FROM media_assets WHERE task_id=%s',(tid,)).fetchone()[0]>=2
    assert db.execute('SELECT credits_available FROM users WHERE id=%s',(uid,)).fetchone()[0]==2


def test_invalid_second_language_does_not_partially_publish(result_db):
    from app import results
    db=result_db;uid,tid,gen=claimed(db)
    invalid=candidate('en');invalid['summary']=''
    with pytest.raises(results.OutputError):results.publish(tid,gen,'result-test-worker',{'zh':candidate(),'en':invalid})
    assert db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='PROCESSING'
    assert db.execute('SELECT count(*) FROM task_results').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM media_assets').fetchone()[0]==0


@pytest.mark.parametrize('invalidate',['cancel','delete','generation','expiry','owner','disabled'])
def test_publication_rejects_stale_cancelled_deleted_or_disabled_owner(result_db,invalidate):
    from app import tasks
    db=result_db;uid,tid,gen=claimed(db)
    if invalidate=='cancel':tasks.cancel_task(tid,uid)
    elif invalidate=='delete':db.execute('UPDATE tasks SET deletion_requested_at=now() WHERE id=%s',(tid,))
    elif invalidate=='generation':db.execute('UPDATE tasks SET lease_generation=lease_generation+1 WHERE id=%s',(tid,))
    elif invalidate=='expiry':db.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 second' WHERE id=%s",(tid,))
    elif invalidate=='owner':db.execute("UPDATE tasks SET lease_owner='replacement' WHERE id=%s",(tid,))
    elif invalidate=='disabled':db.execute('UPDATE users SET disabled_at=now() WHERE id=%s',(uid,))
    with pytest.raises((HTTPException,RuntimeError)):publish(tid,gen)
    assert db.execute('SELECT count(*) FROM task_results').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM media_assets').fetchone()[0]==0


def client():
    from app.main import app
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000')


def authenticated(db,c,uid):
    from app import auth
    token=uuid4().hex
    db.execute("INSERT INTO sessions(user_id,token_hash,expires_at) VALUES (%s,%s,now()+interval '1 hour')",(uid,auth.digest(token)))
    c.cookies.set(auth.COOKIE_NAME,token)


def test_result_export_owner_isolation_and_admin_metadata(result_db):
    db=result_db;uid,tid,gen=claimed(db);publish(tid,gen)
    outsider=identity(db);admin=identity(db,'ADMIN')
    async def scenario():
        async with client() as c:
            assert (await c.get(f'/tasks/{tid}/export?language=zh')).status_code==401
            authenticated(db,c,outsider)
            assert (await c.get(f'/tasks/{tid}/result?language=zh')).status_code in (403,404)
            assert (await c.get(f'/tasks/{tid}/export?language=en')).status_code in (403,404)
            authenticated(db,c,admin)
            assert (await c.get(f'/tasks/{tid}/result?language=zh')).status_code in (403,404)
            metadata=await c.get('/admin/tasks')
            assert metadata.status_code==200
            text=metadata.text
            for secret in ('离线替身摘要','Offline synthetic summary','overview','key_events','storage_key','password_hash'):
                assert secret not in text
            authenticated(db,c,uid)
            response=await c.get(f'/tasks/{tid}/result?language=en')
            assert response.status_code==200,response.text
            assert response.json()['offline_test'] is True
            export=await c.get(f'/tasks/{tid}/export?language=en')
            assert export.status_code==200 and export.json()['language']=='en'
            assert 'attachment' in export.headers['content-disposition']
            assert export.headers['cache-control']=='no-store'
            assert str(uid) not in export.text and str(tid) not in export.text
            assert (await c.get(f'/tasks/{tid}/export?language=fr')).status_code==422
            db.execute('UPDATE tasks SET deletion_requested_at=now() WHERE id=%s',(tid,))
            assert (await c.get(f'/tasks/{tid}/export?language=en')).status_code in (403,404)
    asyncio.run(scenario())


def test_no_download_for_unfinished_task(result_db):
    db=result_db;uid,tid,gen=claimed(db)
    async def scenario():
        async with client() as c:
            authenticated(db,c,uid)
            assert (await c.get(f'/tasks/{tid}/export?language=zh')).status_code in (404,409)
    asyncio.run(scenario())


def test_only_explicit_whole_json_fence_repair_is_allowed(monkeypatch):
    from app.results import OutputError
    raw=json.dumps(candidate(),ensure_ascii=False)
    monkeypatch.setenv('OUTPUT_REPAIR_ATTEMPTS','1')
    assert validate('```json\n'+raw+'\n```')['export']['language']=='zh'
    with pytest.raises(OutputError):validate('Here is the result: '+raw)
    with pytest.raises(OutputError):validate(raw[:-1]+',"language":"en"}')
    monkeypatch.setenv('OUTPUT_REPAIR_ATTEMPTS','0')
    with pytest.raises(OutputError):validate('```json\n'+raw+'\n```')


def test_corrupt_stored_export_is_not_downloadable(result_db):
    from psycopg.types.json import Jsonb
    db=result_db;uid,tid,gen=claimed(db);publish(tid,gen)
    db.execute('UPDATE task_results SET export_document=%s WHERE task_id=%s',(Jsonb({'schema_version':'1.0','user_id':'private-id'}),tid))
    async def scenario():
        async with client() as c:
            authenticated(db,c,uid)
            response=await c.get(f'/tasks/{tid}/export?language=zh')
            assert response.status_code==409 and 'private-id' not in response.text
    asyncio.run(scenario())


def test_pipeline_invalid_second_language_fails_refunds_once_and_cannot_download(result_db,monkeypatch):
    from app import pipeline, results
    from app.worker import WorkerContext
    from types import SimpleNamespace
    db=result_db;uid,tid,gen=claimed(db)
    bad=candidate('en');bad['key_events'][0]['importance']=1
    response=SimpleNamespace(localized_candidates={'zh':candidate(),'en':bad},model_version='offline-fake-test')
    monkeypatch.setattr(pipeline.processing,'prepare_and_analyze',lambda *args,**kwargs:response)
    context=WorkerContext({'id':str(tid),'generation':gen},'result-test-worker')
    with pytest.raises(results.OutputError):pipeline.run_pipeline(context)
    assert db.execute('SELECT status,failure_category,failure_code FROM tasks WHERE id=%s',(tid,)).fetchone()==('FAILED','INTERNAL','OUTPUT_INVALID')
    assert db.execute('SELECT credits_available FROM users WHERE id=%s',(uid,)).fetchone()[0]==3
    assert db.execute("SELECT count(*) FROM quota_events WHERE task_id=%s AND event_type='REFUND'",(tid,)).fetchone()[0]==1
    context.fail('INTERNAL','OUTPUT_INVALID')
    assert db.execute("SELECT count(*) FROM quota_events WHERE task_id=%s AND event_type='REFUND'",(tid,)).fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM task_results').fetchone()[0]==0
    async def scenario():
        async with client() as c:
            authenticated(db,c,uid)
            assert (await c.get(f'/tasks/{tid}/export?language=zh')).status_code==409
    asyncio.run(scenario())


def test_pipeline_success_commits_then_late_cancel_does_not_override(result_db,monkeypatch):
    from app import pipeline,tasks
    from app.worker import WorkerContext
    from types import SimpleNamespace
    db=result_db;uid,tid,gen=claimed(db)
    response=SimpleNamespace(localized_candidates={'zh':candidate(),'en':candidate('en')},model_version='offline-fake-test')
    monkeypatch.setattr(pipeline.processing,'prepare_and_analyze',lambda *args,**kwargs:response)
    context=WorkerContext({'id':str(tid),'generation':gen},'result-test-worker')
    assert pipeline.run_pipeline(context)['status']=='SUCCEEDED' and context.settled.is_set()
    assert tasks.cancel_task(tid,uid)['status']=='SUCCEEDED'
    assert db.execute('SELECT count(*) FROM task_results').fetchone()[0]==2
    assert db.execute("SELECT count(*) FROM quota_events WHERE event_type='REFUND'").fetchone()[0]==0


def test_pipeline_cancellation_before_publication_prevents_results(result_db,monkeypatch):
    from app import pipeline,tasks,processing
    from app.worker import WorkerContext
    from types import SimpleNamespace
    db=result_db;uid,tid,gen=claimed(db)
    def cancel_during_analysis(*args,**kwargs):
        tasks.cancel_task(tid,uid)
        return SimpleNamespace(localized_candidates={'zh':candidate(),'en':candidate('en')},model_version='offline-fake-test')
    monkeypatch.setattr(pipeline.processing,'prepare_and_analyze',cancel_during_analysis)
    context=WorkerContext({'id':str(tid),'generation':gen},'result-test-worker')
    with pytest.raises(processing.ProcessingCancelled):pipeline.run_pipeline(context)
    assert db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='CANCELLED'
    assert db.execute('SELECT count(*) FROM task_results').fetchone()[0]==0
    assert db.execute('SELECT credits_available FROM users WHERE id=%s',(uid,)).fetchone()[0]==2


def test_queue_processing_times_and_target_have_honest_semantics(result_db,monkeypatch):
    from app import tasks,queue
    db=result_db;uid=identity(db)
    start=datetime.now(timezone.utc)+timedelta(seconds=5)
    clock=[start]
    monkeypatch.setattr(tasks,'utcnow',lambda:clock[0])
    tid=db.execute("INSERT INTO tasks(user_id,input_kind,display_filename,duration_seconds,submitted_at) VALUES (%s,'LOCAL','hour.mp4',3600,%s) RETURNING id",(uid,start-timedelta(seconds=10))).fetchone()[0]
    queued=tasks.get_task(tid,uid)
    assert queued['queue_seconds']==10 and queued['processing_seconds']==0 and queued['target_seconds']==1200
    claim=queue.claim_next('result-test-worker')
    clock[0]+=timedelta(seconds=4)
    active=tasks.get_task(tid,uid)
    assert active['queue_seconds']==10 and active['processing_seconds']==4 and active['progress_percent'] is None
    assert active['submitted_at'].utcoffset()==timedelta(0)
    tasks.fail_task(tid,claim['generation'],'INTERNAL',owner='result-test-worker')
    clock[0]+=timedelta(seconds=6)
    tasks.request_retry(tid,uid,str(uuid4()))
    clock[0]+=timedelta(seconds=3)
    retried=tasks.get_task(tid,uid)
    assert retried['queue_seconds']==3 and retried['processing_seconds']==0


def test_private_video_range_and_revoked_session_during_read(result_db,monkeypatch):
    import io
    from app import results
    db=result_db;uid,tid,gen=claimed(db)
    db.execute("INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key,byte_size) VALUES (%s,%s,'ORIGINAL','original-private',10)",(tid,uid))
    reads=[];revoke=[False]
    class Stream(io.BytesIO):
        def read(self,size=-1):
            data=super().read(size)
            if revoke[0]:db.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s',(uid,))
            return data
    class Store:
        def head_object(self,**kwargs):return {'ContentLength':10}
        def get_object(self,**kwargs):
            reads.append(kwargs)
            first,last=map(int,kwargs['Range'].removeprefix('bytes=').split('-'))
            return {'Body':Stream(b'0123456789'[first:last+1])}
    monkeypatch.setattr(results.object_store,'client',lambda:Store())
    async def scenario():
        async with client() as c:
            authenticated(db,c,uid)
            response=await c.get(f'/tasks/{tid}/video',headers={'Range':'bytes=2-4'})
            assert response.status_code==206 and response.content==b'234'
            assert response.headers['content-range']=='bytes 2-4/10' and reads[-1]['Key']=='original-private'
            assert (await c.get(f'/tasks/{tid}/video',headers={'Range':'bytes=20-30'})).status_code==416
            assert (await c.get(f'/tasks/{tid}/video',headers={'Range':'bytes='+('9'*5000)+'-'})).status_code==416
            revoke[0]=True
            response=await c.get(f'/tasks/{tid}/video',headers={'Range':'bytes=0-3'})
            assert response.content==b''
    asyncio.run(scenario())


