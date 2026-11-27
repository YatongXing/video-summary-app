"""Independent input-boundary integration tests; isolated explicit test database."""
import asyncio
import os
from urllib.parse import quote
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest


@pytest.fixture
def ingest_db(monkeypatch, tmp_path):
    url = os.getenv('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL must name an isolated *_test database')
    if not conninfo_to_dict(url).get('dbname', '').endswith('_test'):
        pytest.fail('Only an explicit *_test database is permitted')
    schema = 'ingest_test_' + uuid4().hex
    with psycopg.connect(url, autocommit=True) as control:
        control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        scoped = make_conninfo(url, options=f'-csearch_path={schema}')
        monkeypatch.setenv('DATABASE_URL', scoped)
        monkeypatch.setenv('IMPORT_STAGING_DIR', str(tmp_path/'staging'))
        monkeypatch.setenv('DELETION_JOURNAL_DIR',str(tmp_path/'journal'))
        monkeypatch.setenv('S3_ENDPOINT_URL','http://wp04-synthetic:9000')
        from app.migrate import run_migrations
        try:
            with psycopg.connect(scoped, autocommit=True) as connection:
                run_migrations(connection)
                import json
                from pathlib import Path
                from app.deletion_control import initialize
                from app import object_store
                initialize()
                runtime=Path(os.environ['DELETION_JOURNAL_DIR'])/'runtime';runtime.mkdir(exist_ok=True)
                (runtime/'producer-ready').write_text(json.dumps({'deployment_id':str(connection.execute('SELECT deployment_id FROM deletion_control_state').fetchone()[0])}))
                monkeypatch.setattr(object_store,'cleanup_client',lambda:SyntheticStorage({}))
                yield connection
        finally:
            control.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def client(token=None):
    from app.main import app
    cookies = {'vsa_session': token} if token else None
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8000',
                             headers={'Origin': 'http://127.0.0.1:8000'}, cookies=cookies)


def identity(connection):
    from app.auth import digest, csrf_token
    token = uuid4().hex
    user = connection.execute('INSERT INTO users(email_normalized,password_hash,credits_available) VALUES (%s,%s,1) RETURNING id',
                              (str(uuid4())+'@example.invalid', 'synthetic-not-for-login')).fetchone()[0]
    connection.execute("INSERT INTO sessions(user_id,token_hash,expires_at) VALUES (%s,%s,now()+interval '1 hour')", (user, digest(token)))
    return user, token, csrf_token(token)


def upload_headers(csrf, rights=True, filename='sample.mp4'):
    return {'X-CSRF-Token': csrf, 'X-Video-Filename': quote(filename),
            'X-Upload-Authorized': 'true' if rights else 'false', 'Content-Type': 'application/octet-stream'}


def assert_no_task_or_charge(connection):
    assert connection.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
    assert connection.execute('SELECT count(*) FROM quota_events').fetchone()[0] == 0
    assert connection.execute('SELECT min(credits_available) FROM users').fetchone()[0] in (None, 1)


def test_inputs_require_login_csrf_and_rights(ingest_db):
    _, token, csrf = identity(ingest_db)
    async def scenario():
        async with client() as anonymous:
            assert (await anonymous.post('/inputs/upload', content=b'bad', headers=upload_headers(csrf))).status_code == 401
            assert (await anonymous.post('/inputs/bilibili', json={'url':'https://example.com/video.mp4', 'rights_confirmed':True})).status_code == 401
        async with client(token) as c:
            assert (await c.post('/inputs/upload', content=b'bad', headers={'Content-Type':'application/octet-stream'})).status_code == 403
            assert (await c.post('/inputs/upload', content=b'bad', headers=upload_headers(csrf, False))).status_code in (400, 403, 422)
            assert (await c.post('/inputs/bilibili', json={'url':'https://example.com/video.mp4','rights_confirmed':False}, headers={'X-CSRF-Token':csrf})).status_code in (400,403,422)
    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)


def test_removed_direct_url_api_and_disabled_bilibili(ingest_db, monkeypatch):
    _, token, csrf = identity(ingest_db)
    monkeypatch.setenv('BILIBILI_IMPORT_ENABLED', 'false')

    async def scenario():
        async with client(token) as c:
            body = {'url': 'https://www.bilibili.com/video/BV1iW4113786/', 'rights_confirmed': True}
            headers = {'X-CSRF-Token': csrf}
            assert (await c.post('/inputs/url', json=body, headers=headers)).status_code == 404
            disabled = await c.post('/inputs/bilibili', json=body, headers=headers)
            assert disabled.status_code == 404 and disabled.json()['code'] == 'BILIBILI_DISABLED'
    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)


def test_rejected_url_inputs_are_safe_and_do_not_enqueue(ingest_db):
    _, token, csrf = identity(ingest_db)
    urls = ['http://example.com/video.mp4', 'https://user:private-password@example.com/video.mp4',
            'https://127.0.0.1/video.mp4', 'https://[::1]/video.mp4', 'https://169.254.169.254/latest',
            'https://10.1.2.3/video.mp4', 'https://example.com:8443/video.mp4',
            'https://www.bilibili.com/video/BV1iW4113786/?p=1&p=2',
            'https://www.bilibili.com/video/BV1iW4113786/?p=0']
    async def scenario():
        async with client(token) as c:
            for url in urls:
                response=await c.post('/inputs/bilibili', json={'url':url,'rights_confirmed':True}, headers={'X-CSRF-Token':csrf})
                assert 400 <= response.status_code < 500, response.text
                assert 'private-password' not in response.text
                assert url not in response.text
    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)


def test_013_preserves_local_rows_or_refuses_unexpected_url(ingest_db, monkeypatch, tmp_path):
    import shutil
    from app import migrate

    files = migrate.MIGRATIONS_DIR
    baseline = tmp_path/'baseline'
    baseline.mkdir()
    migration = files/'013_bilibili_input.sql'
    for path in files.glob('*.sql'):
        if path.name < '013_':
            shutil.copyfile(path, baseline/path.name)
    schema = sql.Identifier('stagec_upgrade_' + uuid4().hex)
    ingest_db.execute(sql.SQL('CREATE SCHEMA {}').format(schema))
    try:
        ingest_db.execute(sql.SQL('SET search_path TO {}').format(schema))
        monkeypatch.setattr(migrate, 'MIGRATIONS_DIR', baseline)
        migrate.run_migrations(ingest_db)
        user, _, _ = identity(ingest_db)
        local = ingest_db.execute("INSERT INTO tasks(user_id,input_kind) VALUES (%s,'LOCAL') RETURNING id", (user,)).fetchone()[0]
        legacy = ingest_db.execute("INSERT INTO tasks(user_id,input_kind) VALUES (%s,'URL') RETURNING id", (user,)).fetchone()[0]
        shutil.copyfile(migration, baseline/migration.name)
        with pytest.raises(psycopg.errors.CheckViolation):
            migrate.run_migrations(ingest_db)
        assert ingest_db.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 12
        assert ingest_db.execute("SELECT input_kind FROM tasks WHERE id=%s", (legacy,)).fetchone()[0] == 'URL'
        ingest_db.execute('DELETE FROM tasks WHERE id=%s', (legacy,))
        assert migrate.run_migrations(ingest_db) == [migration.name]
        assert ingest_db.execute("SELECT input_kind FROM tasks WHERE id=%s", (local,)).fetchone()[0] == 'LOCAL'
        old_bili = ingest_db.execute("INSERT INTO tasks(user_id,input_kind) VALUES (%s,'BILIBILI') RETURNING id",
                                  (user,)).fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            ingest_db.execute("INSERT INTO tasks(user_id,input_kind) VALUES (%s,'URL')", (user,))
        pilot = files/'014_bilibili_evidence.sql'
        shutil.copyfile(pilot, baseline/pilot.name)
        assert migrate.run_migrations(ingest_db) == [pilot.name]
        for original in (local, old_bili):
            assert ingest_db.execute(
                'SELECT bilibili_evidence,bilibili_transcript FROM tasks WHERE id=%s',
                (original,)).fetchone() == ('FULL_MEDIA', None)
        with pytest.raises(psycopg.errors.CheckViolation):
            ingest_db.execute("UPDATE tasks SET bilibili_evidence='AUDIO_ONLY' WHERE id=%s", (local,))
    finally:
        ingest_db.execute('RESET search_path')
        ingest_db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(schema))


@pytest.mark.parametrize('mode,code,status',[
    ('truncated','BILIBILI_NO_MEDIA',422),('timeout','BILIBILI_TIMEOUT',504),
])
def test_bilibili_transport_failure_cleans_staging(ingest_db,monkeypatch,tmp_path,mode,code,status):
    from app import bilibili_import
    from app.ingest import staging_dir

    _,token,csrf=identity(ingest_db)
    socket_path=tmp_path/'download.sock'
    monkeypatch.setenv('BILIBILI_DOWNLOAD_SOCKET',str(socket_path))
    if mode=='timeout':
        monkeypatch.setattr(bilibili_import,'IMPORT_SECONDS',.25)

    async def scenario():
        closed=asyncio.Event()

        async def transport(reader,writer):
            try:
                request=await reader.readuntil(b'\r\n\r\n')
                assert request.startswith(b'GET /BV1iW4113786/1/auto ')
                if mode=='truncated':
                    writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n'
                                 b'X-Import-Source: BV1iW4113786:1\r\nX-Import-Duration: 2\r\nX-Import-Evidence: AUDIO_ONLY\r\n\r\n'
                                 + b'a'*70000)
                    await writer.drain()
                else:
                    assert await reader.read() == b''
            finally:
                writer.close()
                await writer.wait_closed()
                closed.set()

        async with await asyncio.start_unix_server(transport,path=socket_path):
            async with client(token) as c:
                response=await c.post('/inputs/bilibili',json={
                    'url':'https://www.bilibili.com/video/BV1iW4113786/', 'rights_confirmed':True},
                    headers={'X-CSRF-Token':csrf})
            assert response.status_code==status and response.json()['code']==code
            await asyncio.wait_for(closed.wait(),2)

    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)
    assert ingest_db.execute('SELECT count(*) FROM input_imports').fetchone()[0]==0
    assert not list(staging_dir().iterdir())


def test_bilibili_recovery_removes_expired_registered_staging(ingest_db):
    from app.auth import Principal
    from app.ingest import begin_import, recover_staging

    user,_,_=identity(ingest_db)
    session=ingest_db.execute('SELECT id FROM sessions WHERE user_id=%s',(user,)).fetchone()[0]
    principal=Principal(user,'synthetic@example.invalid','USER',session)
    identifier,_,path=begin_import(principal,expires_in=0)
    path.write_bytes(b'interrupted Bilibili staging')
    recover_staging()
    assert not path.exists()
    assert ingest_db.execute('SELECT count(*) FROM input_imports WHERE id=%s',(identifier,)).fetchone()[0]==0
    assert_no_task_or_charge(ingest_db)


def test_bilibili_client_disconnect_closes_downloader_and_cleans(ingest_db,monkeypatch,tmp_path):
    import json
    from app.ingest import staging_dir
    from app.main import app

    _,token,csrf=identity(ingest_db)
    address=tmp_path/'download.sock'
    monkeypatch.setenv('BILIBILI_DOWNLOAD_SOCKET',str(address))

    async def scenario():
        started=asyncio.Event()
        closed=asyncio.Event()
        request=asyncio.Queue()
        sent=[]

        async def downloader(reader,writer):
            try:
                await reader.readuntil(b'\r\n\r\n')
                started.set()
                assert await reader.read()==b''
            finally:
                writer.close()
                await writer.wait_closed()
                closed.set()

        async def send(event):
            sent.append(event)

        body=json.dumps({'url':'https://www.bilibili.com/video/BV1iW4113786/',
                         'rights_confirmed':True}).encode()
        await request.put({'type':'http.request','body':body})
        scope={'type':'http','method':'POST','path':'/inputs/bilibili','query_string':b'',
               'scheme':'http','client':('127.0.0.1',12345),'server':('127.0.0.1',8000),
               'headers':[(b'origin',b'http://127.0.0.1:8000'),(b'cookie',f'vsa_session={token}'.encode()),
                          (b'content-type',b'application/json'),(b'x-csrf-token',csrf.encode())]}
        async with await asyncio.start_unix_server(downloader,path=address):
            operation=asyncio.create_task(app(scope,request.get,send))
            await asyncio.wait_for(started.wait(),2)
            await request.put({'type':'http.disconnect'})
            await asyncio.wait_for(operation,3)
            await asyncio.wait_for(closed.wait(),2)
        assert sent[0]['status']==499

    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)
    assert ingest_db.execute('SELECT count(*) FROM input_imports').fetchone()[0]==0
    assert not list(staging_dir().iterdir())
def test_non_media_uploads_are_rejected_without_charge(ingest_db):
    _, token, csrf = identity(ingest_db)
    async def scenario():
        async with client(token) as c:
            for payload in (b'', b'<html>platform video player</html>', b'not a video'):
                response=await c.post('/inputs/upload', content=payload, headers=upload_headers(csrf))
                assert 400 <= response.status_code < 500, response.text
                assert 'Traceback' not in response.text
    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)


@pytest.fixture
def valid_video(tmp_path):
    import shutil, subprocess
    ffmpeg=os.getenv('FFMPEG_BIN','ffmpeg')
    if not shutil.which(ffmpeg):pytest.skip('Run Docker test target for real media fixtures')
    path=tmp_path/'valid.mp4'
    subprocess.run([ffmpeg,'-nostdin','-y','-v','error','-f','lavfi','-i','color=c=blue:s=32x32:r=10:d=1','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-movflags','+faststart',str(path)],capture_output=True,check=True,timeout=30)
    return path.read_bytes()

@pytest.fixture
def private_store(monkeypatch):
    from app import object_store
    objects={}
    monkeypatch.setattr(object_store,'put_original',lambda path,key,**_options:objects.__setitem__(key,path.read_bytes()))
    monkeypatch.setattr(object_store,'delete_original',lambda key:objects.pop(key,None))
    return objects


@pytest.mark.parametrize('model',[None,'deepseek-v4.1-flash'])
def test_valid_upload_and_url_become_owned_queued_tasks_without_spend(ingest_db,valid_video,private_store,monkeypatch,tmp_path,model):
    from app import bilibili_import
    monkeypatch.setenv('DEEPSEEK_ENABLED','true')
    expected_model=model or 'qwen3.8-omni-flash'
    user,token,csrf=identity(ingest_db)
    import subprocess
    local = tmp_path/'local.mp4'
    local.write_bytes(valid_video)
    with_audio = tmp_path/'with_audio.mp4'
    subprocess.run([os.getenv('FFMPEG_BIN','ffmpeg'),'-nostdin','-v','error','-i',str(local),
                    '-f','lavfi','-i','anullsrc','-c:v','copy','-c:a','aac','-shortest',str(with_audio)],
                   check=True,timeout=30)

    async def fixture_download(source, path, *, combine_visual):
        assert source == ('BV1iW4113786', 2) and combine_visual
        path.write_bytes(with_audio.read_bytes())
        return 1.0, 'FULL_MEDIA'

    monkeypatch.setattr(bilibili_import,'download',fixture_download)
    async def scenario():
        async with client(token) as c:
            headers=upload_headers(csrf,filename='../private/video.mp4')
            if model:
                headers['X-Video-Model']=model
            uploaded=await c.post('/inputs/upload',content=valid_video,headers=headers)
            assert uploaded.status_code==201,uploaded.text
            assert uploaded.json()['filename']=='video.mp4'
            assert uploaded.json()['has_audio'] is False
            body={'url':'https://www.bilibili.com/video/BV1iW4113786/?p=2&secret=private','rights_confirmed':True,'combine_visual':True}
            if model:
                body['model']=model
            imported=await c.post('/inputs/bilibili',json=body,headers={'X-CSRF-Token':csrf})
            assert imported.status_code==201,imported.text
            assert 'secret' not in imported.text and 'private' not in imported.text
            for response in (uploaded,imported):
                data=response.json();assert data['status']=='QUEUED'
                assert data['requested_model']==expected_model
                owner_result=await c.get('/tasks/'+data['id'])
                assert owner_result.status_code==200 and owner_result.json()['requested_model']==expected_model
    asyncio.run(scenario())
    rows=ingest_db.execute('SELECT user_id,started_at,requested_model FROM tasks').fetchall()
    assert len(rows)==2 and all(row==(user,None,expected_model) for row in rows)
    assert len(private_store)==2
    assert ingest_db.execute('SELECT count(*) FROM media_assets').fetchone()[0]==2
    assert ingest_db.execute('SELECT count(*) FROM quota_events').fetchone()[0]==0
    assert ingest_db.execute('SELECT count(*) FROM input_imports').fetchone()[0]==0
    from app.ingest import staging_dir
    assert not list(staging_dir().iterdir())


@pytest.mark.parametrize('model,status,code',[
    ('deepseek-v4.1-flash',409,'MODEL_DISABLED'),
    ('unapproved-model',422,'INVALID_MODEL'),
])
def test_unavailable_model_rejected_before_import(ingest_db,monkeypatch,model,status,code):
    from app import ingest
    monkeypatch.delenv('DEEPSEEK_ENABLED',raising=False)
    monkeypatch.setattr(ingest,'begin_import',lambda user:pytest.fail('Must reject before allocating staging'))
    _,token,csrf=identity(ingest_db)

    async def scenario():
        async with client(token) as c:
            headers={**upload_headers(csrf),'X-Video-Model':model}
            local=await c.post('/inputs/upload',content=b'not read',headers=headers)
            remote=await c.post('/inputs/bilibili',json={'url':'https://example.com/video.mp4','rights_confirmed':True,'model':model},
                                headers={'X-CSRF-Token':csrf})
            assert all(result.status_code==status and result.json()['code']==code for result in (local,remote))
    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)


def test_streamed_bytes_are_bounded_without_content_length(ingest_db,monkeypatch,tmp_path):
    from app import media
    monkeypatch.setattr(media,'MAX_BYTES',5)
    _,token,csrf=identity(ingest_db)
    async def chunks():
        yield b'123';yield b'456'
    async def scenario():
        async with client(token) as c:
            response=await c.post('/inputs/upload',content=chunks(),headers=upload_headers(csrf))
            assert response.status_code==413,response.text
    asyncio.run(scenario())
    from app.ingest import recover_staging
    recover_staging()
    assert_no_task_or_charge(ingest_db)
    assert ingest_db.execute('SELECT count(*) FROM input_imports').fetchone()[0]==0
    from app.ingest import staging_dir
    assert not list(staging_dir().iterdir())


def test_failed_object_cleanup_remains_registered_until_recovery(ingest_db,valid_video,monkeypatch,tmp_path):
    from app import object_store,ingest
    user,token,csrf=identity(ingest_db); objects={}
    def upload_then_revoke(path,key):
        objects[key]=path.read_bytes()
        ingest_db.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s',(user,))
    def failed_delete(key):raise OSError('synthetic cleanup failure')
    monkeypatch.setattr(object_store,'put_original',upload_then_revoke)
    storage=SyntheticStorage(objects);storage.fail_delete=True
    monkeypatch.setattr(object_store,'cleanup_client',lambda:storage)
    async def scenario():
        async with client(token) as c:
            response=await c.post('/inputs/upload',content=valid_video,headers=upload_headers(csrf))
            assert response.status_code==401,response.text
    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)
    rows=ingest_db.execute('SELECT state FROM input_imports').fetchall()
    assert rows==[('CLEANUP_REQUIRED',)],'Unconfirmed object deletion must keep the recovery ledger'
    assert len(objects)==1
    storage.fail_delete=False
    ingest.recover_staging()
    assert not objects
    assert ingest_db.execute('SELECT count(*) FROM input_imports').fetchone()[0]==0


def test_disconnected_upload_removes_partial_staging(ingest_db,tmp_path):
    from starlette.requests import Request
    from app.media import InputError
    from app.auth import Principal
    from app import ingest
    user,_,_=identity(ingest_db)
    session=ingest_db.execute('SELECT id FROM sessions WHERE user_id=%s',(user,)).fetchone()[0]
    principal=Principal(user,'synthetic@example.invalid','USER',session)
    messages=iter([{'type':'http.request','body':b'partial media','more_body':True},{'type':'http.disconnect'}])
    async def receive():return next(messages)
    request=Request({'type':'http','method':'POST','path':'/inputs/upload','headers':[(b'x-upload-authorized',b'true'),(b'x-video-filename',b'video.mp4')],'scheme':'http','server':('127.0.0.1',8000),'client':('127.0.0.1',123)},receive)
    async def scenario():
        with pytest.raises(InputError) as error:await ingest.upload(request,principal)
        assert error.value.code=='IMPORT_FAILED'
    asyncio.run(scenario())
    from app.ingest import recover_staging
    recover_staging()
    assert_no_task_or_charge(ingest_db)
    assert ingest_db.execute('SELECT count(*) FROM input_imports').fetchone()[0]==0
    from app.ingest import staging_dir
    assert not list(staging_dir().iterdir())



def test_stale_cleanup_skips_a_renewed_import(ingest_db,monkeypatch,tmp_path):
    from app.auth import Principal
    from app import ingest,object_store
    user,_,_=identity(ingest_db)
    session=ingest_db.execute('SELECT id FROM sessions WHERE user_id=%s',(user,)).fetchone()[0]
    identifier,key,path=ingest.begin_import(Principal(user,'synthetic@example.invalid','USER',session))
    path.write_bytes(b'synthetic pending object')
    ingest_db.execute("UPDATE input_imports SET state='STORING',expires_at=now()-interval '1 minute' WHERE id=%s",(identifier,))
    ingest.renew_import(identifier)
    deleted=[];monkeypatch.setattr(object_store,'delete_original',deleted.append)
    monkeypatch.setattr(object_store,'cleanup_client',lambda:pytest.fail('Renewed/finalized import must not call storage'))
    ingest.cleanup_import(identifier,key,path,True,stale_only=True)
    assert not deleted and path.exists()
    assert ingest_db.execute('SELECT count(*) FROM input_imports WHERE id=%s',(identifier,)).fetchone()[0]==1


def test_stale_cleanup_cannot_delete_a_finalized_registered_object(ingest_db,monkeypatch,tmp_path):
    from app.auth import Principal
    from app import ingest,object_store
    user,_,_=identity(ingest_db)
    session=ingest_db.execute('SELECT id FROM sessions WHERE user_id=%s',(user,)).fetchone()[0]
    identifier,key,path=ingest.begin_import(Principal(user,'synthetic@example.invalid','USER',session))
    path.write_bytes(b'synthetic stored object')
    # The finalize transaction atomically registers the asset and removes its import ledger.
    with ingest_db.transaction():
        task=ingest_db.execute("INSERT INTO tasks(user_id,input_kind) VALUES (%s,'LOCAL') RETURNING id",(user,)).fetchone()[0]
        ingest_db.execute("INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key) VALUES (%s,%s,'ORIGINAL',%s)",(task,user,key))
        ingest_db.execute('DELETE FROM input_imports WHERE id=%s',(identifier,))
    deleted=[];monkeypatch.setattr(object_store,'delete_original',deleted.append)
    monkeypatch.setattr(object_store,'cleanup_client',lambda:pytest.fail('Renewed/finalized import must not call storage'))
    ingest.cleanup_import(identifier,key,path,True,stale_only=True)
    assert not deleted
    assert ingest_db.execute('SELECT count(*) FROM media_assets WHERE storage_key=%s',(key,)).fetchone()[0]==1

class SyntheticStorage:
    def __init__(self,objects):self.objects=objects;self.fail_delete=False
    def head_bucket(self,**kwargs):pass
    def get_bucket_versioning(self,**kwargs):return {}
    def list_multipart_uploads(self,**kwargs):return {'Uploads':[]}
    def delete_object(self,**kwargs):
        if self.fail_delete:raise OSError('synthetic cleanup failure')
        self.objects.pop(kwargs['Key'],None)
    def head_object(self,**kwargs):
        from botocore.exceptions import ClientError
        if kwargs['Key'] in self.objects:return {'ContentLength':len(self.objects[kwargs['Key']])}
        raise ClientError({'Error':{'Code':'NoSuchKey'}},'HeadObject')
