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
        monkeypatch.setenv('IMPORT_STAGING_DIR', str(tmp_path))
        from app.migrate import run_migrations
        try:
            with psycopg.connect(scoped, autocommit=True) as connection:
                run_migrations(connection)
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
    user = connection.execute('INSERT INTO users(email_normalized,password_hash) VALUES (%s,%s) RETURNING id',
                              (str(uuid4())+'@example.invalid', 'synthetic-not-for-login')).fetchone()[0]
    connection.execute("INSERT INTO sessions(user_id,token_hash,expires_at) VALUES (%s,%s,now()+interval '1 hour')", (user, digest(token)))
    return user, token, csrf_token(token)


def upload_headers(csrf, rights=True, filename='sample.mp4'):
    return {'X-CSRF-Token': csrf, 'X-Video-Filename': quote(filename),
            'X-Upload-Authorized': 'true' if rights else 'false', 'Content-Type': 'application/octet-stream'}


def assert_no_task_or_charge(connection):
    assert connection.execute('SELECT count(*) FROM tasks').fetchone()[0] == 0
    assert connection.execute('SELECT count(*) FROM quota_events').fetchone()[0] == 0
    assert connection.execute('SELECT min(credits_available) FROM users').fetchone()[0] in (None, 3)


def test_inputs_require_login_csrf_and_rights(ingest_db):
    _, token, csrf = identity(ingest_db)
    async def scenario():
        async with client() as anonymous:
            assert (await anonymous.post('/inputs/upload', content=b'bad', headers=upload_headers(csrf))).status_code == 401
            assert (await anonymous.post('/inputs/url', json={'url':'https://example.com/video.mp4', 'rights_confirmed':True})).status_code == 401
        async with client(token) as c:
            assert (await c.post('/inputs/upload', content=b'bad', headers={'Content-Type':'application/octet-stream'})).status_code == 403
            assert (await c.post('/inputs/upload', content=b'bad', headers=upload_headers(csrf, False))).status_code in (400, 403, 422)
            assert (await c.post('/inputs/url', json={'url':'https://example.com/video.mp4','rights_confirmed':False}, headers={'X-CSRF-Token':csrf})).status_code in (400,403,422)
    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)


def test_rejected_url_inputs_are_safe_and_do_not_enqueue(ingest_db):
    _, token, csrf = identity(ingest_db)
    urls = ['http://example.com/video.mp4', 'https://user:private-password@example.com/video.mp4',
            'https://127.0.0.1/video.mp4', 'https://[::1]/video.mp4', 'https://169.254.169.254/latest',
            'https://10.1.2.3/video.mp4', 'https://example.com:8443/video.mp4']
    async def scenario():
        async with client(token) as c:
            for url in urls:
                response=await c.post('/inputs/url', json={'url':url,'rights_confirmed':True}, headers={'X-CSRF-Token':csrf})
                assert 400 <= response.status_code < 500, response.text
                assert 'private-password' not in response.text
                assert url not in response.text
    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)


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
    monkeypatch.setattr(object_store,'put_original',lambda path,key:objects.__setitem__(key,path.read_bytes()))
    monkeypatch.setattr(object_store,'delete_original',lambda key:objects.pop(key,None))
    return objects


def test_valid_upload_and_url_become_owned_queued_tasks_without_spend(ingest_db,valid_video,private_store,monkeypatch,tmp_path):
    from app import safe_download
    user,token,csrf=identity(ingest_db)
    monkeypatch.setattr(safe_download,'download_video',lambda url,path,max_bytes:path.write_bytes(valid_video))
    async def scenario():
        async with client(token) as c:
            uploaded=await c.post('/inputs/upload',content=valid_video,headers=upload_headers(csrf,filename='../private/video.mp4'))
            assert uploaded.status_code==201,uploaded.text
            assert uploaded.json()['filename']=='video.mp4'
            assert uploaded.json()['has_audio'] is False
            imported=await c.post('/inputs/url',json={'url':'https://example.com/video?secret=private','rights_confirmed':True},headers={'X-CSRF-Token':csrf})
            assert imported.status_code==201,imported.text
            assert 'secret' not in imported.text and 'private' not in imported.text
            for response in (uploaded,imported):
                data=response.json();assert data['status']=='QUEUED'
                assert (await c.get('/tasks/'+data['id'])).status_code==200
    asyncio.run(scenario())
    rows=ingest_db.execute('SELECT user_id,started_at FROM tasks').fetchall()
    assert len(rows)==2 and all(row[0]==user and row[1] is None for row in rows)
    assert len(private_store)==2
    assert ingest_db.execute('SELECT count(*) FROM media_assets').fetchone()[0]==2
    assert ingest_db.execute('SELECT count(*) FROM quota_events').fetchone()[0]==0
    assert ingest_db.execute('SELECT count(*) FROM input_imports').fetchone()[0]==0
    assert not list(tmp_path.iterdir()) or all(p.name=='valid.mp4' for p in tmp_path.iterdir())


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
    assert_no_task_or_charge(ingest_db)
    assert ingest_db.execute('SELECT count(*) FROM input_imports').fetchone()[0]==0
    assert not list(tmp_path.iterdir())


def test_failed_object_cleanup_remains_registered_until_recovery(ingest_db,valid_video,monkeypatch,tmp_path):
    from app import object_store,ingest
    user,token,csrf=identity(ingest_db); objects={}
    def upload_then_revoke(path,key):
        objects[key]=path.read_bytes()
        ingest_db.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s',(user,))
    def failed_delete(key):raise OSError('synthetic cleanup failure')
    monkeypatch.setattr(object_store,'put_original',upload_then_revoke)
    monkeypatch.setattr(object_store,'delete_original',failed_delete)
    async def scenario():
        async with client(token) as c:
            response=await c.post('/inputs/upload',content=valid_video,headers=upload_headers(csrf))
            assert response.status_code==401,response.text
    asyncio.run(scenario())
    assert_no_task_or_charge(ingest_db)
    rows=ingest_db.execute('SELECT state FROM input_imports').fetchall()
    assert rows==[('CLEANUP_REQUIRED',)],'Unconfirmed object deletion must keep the recovery ledger'
    assert len(objects)==1
    monkeypatch.setattr(object_store,'delete_original',lambda key:objects.pop(key,None))
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
    assert_no_task_or_charge(ingest_db)
    assert ingest_db.execute('SELECT count(*) FROM input_imports').fetchone()[0]==0
    assert not list(tmp_path.iterdir())



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
    ingest.cleanup_import(identifier,key,path,True,stale_only=True)
    assert not deleted
    assert ingest_db.execute('SELECT count(*) FROM media_assets WHERE storage_key=%s',(key,)).fetchone()[0]==1
