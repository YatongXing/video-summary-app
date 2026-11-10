"""Authenticated private import; queue admission follows full media validation."""
from app import maintenance
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import threading
import time
from urllib.parse import unquote
from uuid import uuid4
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.concurrency import run_in_threadpool
from app.auth import Principal, connect, mutation_user
from app import media, safe_download, object_store
router=APIRouter()

def staging_dir():
    path=Path(os.getenv('IMPORT_STAGING_DIR','/var/lib/video-summary-app/staging'))
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    return path

def staging_backend():
    import hashlib
    p=staging_dir()
    if p.is_symlink():raise ValueError('Unsafe staging root')
    st=p.stat()
    return 'STAGING:'+hashlib.sha256((str(p.resolve())+':'+str(st.st_dev)+':'+str(st.st_ino)).encode()).hexdigest()

def import_location(row):
    return (row['storage_backend']==object_store.backend_id() and row['storage_bucket']==object_store.bucket()
            and row['storage_version'] is None and row['staging_backend']==staging_backend())

def active_session(connection,user):
    maintenance.ensure_open()
    found=connection.execute('''SELECT s.id FROM sessions s JOIN users u ON u.id=s.user_id
        WHERE s.id=%s AND s.user_id=%s AND s.revoked_at IS NULL AND s.expires_at>now()
        AND u.disabled_at IS NULL''',(user.session_id,user.id)).fetchone()
    if found is None: raise media.InputError('SESSION_EXPIRED','登录已失效，请重新登录。',401)

def begin_import(user):
    maintenance.ensure_open()
    identifier=uuid4(); key=f'originals/{user.id}/{identifier}'
    path=staging_dir()/str(identifier)
    with connect() as connection:
        connection.execute('SELECT pg_advisory_xact_lock(773504001)')
        active_session(connection,user)
        count=connection.execute("SELECT count(*) AS total FROM input_imports WHERE state IN ('STAGING','STORING') AND expires_at>now()").fetchone()['total']
        if count>=int(os.getenv('IMPORT_CONCURRENCY','5')):
            raise media.InputError('IMPORT_BUSY','当前导入较多，请稍后重试。',429)
        connection.execute('INSERT INTO input_imports(id,user_id,object_key,storage_backend,storage_bucket,staging_backend) VALUES (%s,%s,%s,%s,%s,%s)',(identifier,user.id,key,object_store.backend_id(),object_store.bucket(),staging_backend()))
        maintenance.ensure_open()
    return identifier,key,path

def cleanup_import(identifier,key,path,stored,stale_only=False):
    from app import task_locks
    from app.cleanup import io_activity
    if not maintenance.ready():return
    try:
        if not task_locks.producer_ready():return
        with task_locks.cleanup_global(),task_locks.exclusive('import-'+str(identifier)),io_activity(identifier):
            with connect() as connection:
                row=connection.execute('SELECT * FROM input_imports WHERE id=%s',(identifier,)).fetchone()
                if not row or stale_only and row['expires_at']>__import__('datetime').datetime.now(__import__('datetime').timezone.utc):return
                if not import_location(row) or key!=row['object_key'] or path!=staging_dir()/str(identifier) or path.is_symlink():raise ValueError('Import ownership mismatch')
                stored=stored or row['state']!='STAGING'
            if stored:
                from app.cleanup import _delete_object
                _delete_object({'storage_backend':row['storage_backend'],'storage_bucket':row['storage_bucket'],'storage_key':row['object_key'],'storage_version':row['storage_version'],'user_id':row['user_id']})
            path.unlink(missing_ok=True)
            with connect() as connection:connection.execute('DELETE FROM input_imports WHERE id=%s',(identifier,))
    except task_locks.LockBusy:
        with connect() as connection:
            connection.execute("UPDATE input_imports SET state='CLEANUP_REQUIRED',cleanup_error_code='PRODUCER_BUSY',expires_at=clock_timestamp() WHERE id=%s",(identifier,))
        return
    except Exception:
        with connect() as connection:connection.execute("UPDATE input_imports SET state='CLEANUP_REQUIRED',cleanup_error_code='CLEANUP_FAILED',expires_at=now() WHERE id=%s",(identifier,))

def renew_import(identifier):
    maintenance.ensure_open()
    with connect() as connection:
        connection.execute("UPDATE input_imports SET expires_at=now()+interval '6 hours' WHERE id=%s AND state IN ('STAGING','STORING')",(identifier,))
        maintenance.ensure_open()

def recover_staging():
    maintenance.ensure_open()
    with connect() as connection:
        rows=connection.execute('SELECT id,object_key,state FROM input_imports WHERE expires_at<=now()').fetchall()
    for row in rows:
        cleanup_import(row['id'],row['object_key'],staging_dir()/str(row['id']),row['state']!='STAGING',True)
@asynccontextmanager
async def lifespan(_app):
    from app.deletion_control import replay_journal
    if maintenance.ready():
        try:await run_in_threadpool(replay_journal)
        except maintenance.MaintenanceClosed:pass
    async def loop():
        while True:
            try: await run_in_threadpool(recover_staging)
            except Exception: pass
            await asyncio.sleep(60)
    task=asyncio.create_task(loop())
    try: yield
    finally:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

def safe_filename(value):
    if len(value)>2048: raise media.InputError('INVALID_FILENAME','文件名无效。')
    try: name=unquote(value,errors='strict').replace('\\','/').split('/')[-1]
    except UnicodeError: raise media.InputError('INVALID_FILENAME','文件名无效。') from None
    name=''.join(c for c in name if c.isprintable()).strip().strip('.')[:255]
    return name or 'video'

def finish_import(user,identifier,key,path,info,name,cancelled):
    maintenance.ensure_open()
    if cancelled.is_set(): raise media.InputError('IMPORT_CANCELLED','导入已中断。',499)
    with connect() as connection:
        active_session(connection,user)
        row=connection.execute('SELECT * FROM input_imports WHERE id=%s AND expires_at>now() FOR UPDATE',(identifier,)).fetchone()
        if row is not None and (not import_location(row) or row['user_id']!=user.id or row['object_key']!=key or path!=staging_dir()/str(identifier)):raise media.InputError('IMPORT_LOCATION_CHANGED',status=503)
        if row is None: raise media.InputError('IMPORT_CANCELLED','导入已中断。',499)
        connection.execute("UPDATE input_imports SET state='STORING' WHERE id=%s",(identifier,))
        maintenance.ensure_open()
    try:
        object_store.put_original(path,key)
        if cancelled.is_set(): raise media.InputError('IMPORT_CANCELLED','导入已中断。',499)
        with connect() as connection:
            ledger=connection.execute("SELECT * FROM input_imports WHERE id=%s AND state='STORING' AND expires_at>now() FOR UPDATE",(identifier,)).fetchone()
            if ledger is not None and (not import_location(ledger) or ledger['user_id']!=user.id or ledger['object_key']!=key):raise media.InputError('IMPORT_LOCATION_CHANGED',status=503)
            if ledger is None: raise media.InputError('IMPORT_CANCELLED','导入已中断。',499)
            active_session(connection,user)
            if cancelled.is_set(): raise media.InputError('IMPORT_CANCELLED','导入已中断。',499)
            row=connection.execute('''INSERT INTO tasks(user_id,input_kind,display_filename,duration_seconds,submitted_at)
                VALUES (%s,%s,%s,%s,clock_timestamp()) RETURNING id,status''',(user.id,info['input_kind'],name,info['media'].duration_seconds)).fetchone()
            connection.execute('''INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key,byte_size,has_audio,storage_backend,storage_bucket,storage_version)
                VALUES (%s,%s,'ORIGINAL',%s,%s,%s,%s,%s,%s)''',(row['id'],user.id,key,path.stat().st_size,info['media'].has_audio,ledger['storage_backend'],ledger['storage_bucket'],ledger['storage_version']))
            connection.execute('DELETE FROM input_imports WHERE id=%s',(identifier,))
            maintenance.ensure_open()
        try: path.unlink(missing_ok=True)
        except OSError: pass
        return {'id':str(row['id']),'status':row['status'],'filename':name,'duration_seconds':info['media'].duration_seconds,'has_audio':info['media'].has_audio}
    except BaseException:
        cleanup_import(identifier,key,path,True)
        raise

async def validate_and_finish(request,user,identifier,key,path,input_kind,filename):
    maintenance.ensure_open()
    cancelled=threading.Event()
    async def watch():
        last_renew=time.monotonic()
        while True:
            if await request.is_disconnected(): cancelled.set(); return
            if time.monotonic()-last_renew>60:
                await run_in_threadpool(renew_import,identifier)
                last_renew=time.monotonic()
            await asyncio.sleep(.1)
    watcher=asyncio.create_task(watch())
    try:
        info=await run_in_threadpool(media.validate_media,path)
        if await request.is_disconnected(): cancelled.set()
        finishing=asyncio.create_task(run_in_threadpool(finish_import,user,identifier,key,path,{'input_kind':input_kind,'media':info},filename or f'imported-video.{info.container}',cancelled))
        try:
            return await asyncio.shield(finishing)
        except BaseException:
            cancelled.set()
            try: await asyncio.shield(finishing)
            except BaseException: pass
            raise
    except BaseException:
        cancelled.set()
        raise
    finally:
        watcher.cancel()
        try: await watcher
        except asyncio.CancelledError: pass

@router.post('/inputs/upload',status_code=201)
async def upload(request:Request,user:Principal=Depends(mutation_user)):
    maintenance.ensure_open()
    if request.headers.get('x-upload-authorized')!='true': raise media.InputError('RIGHTS_REQUIRED','请先确认拥有上传和处理授权。',400)
    filename=safe_filename(request.headers.get('x-video-filename','video'))
    length=request.headers.get('content-length')
    if length:
        try:
            if int(length)<0: raise ValueError()
            if int(length)>media.MAX_BYTES: raise media.InputError('FILE_TOO_LARGE','单个视频不能超过 2 GB。',413)
        except ValueError: raise media.InputError('INVALID_MEDIA','上传长度无效。',400) from None
    identifier,key,path=begin_import(user)
    from app.task_locks import producer
    with producer('import-'+str(identifier)):
        try:
            total=0
            async with asyncio.timeout(max(1,min(3600,int(os.getenv('IMPORT_UPLOAD_TIMEOUT_SECONDS','1800'))))):
                with path.open('xb') as target:
                    async for chunk in request.stream():
                        maintenance.ensure_open()
                        total+=len(chunk)
                        if total>media.MAX_BYTES: raise media.InputError('FILE_TOO_LARGE','单个视频不能超过 2 GB。',413)
                        maintenance.ensure_open()
                        target.write(chunk)
                        maintenance.ensure_open()
            if length is not None and total!=int(length): raise media.InputError('INVALID_MEDIA','视频上传不完整。')
            return await validate_and_finish(request,user,identifier,key,path,'LOCAL',filename)
        except BaseException as error:
            cleanup_import(identifier,key,path,False)
            if isinstance(error, media.InputError): raise
            if isinstance(error, TimeoutError): raise media.InputError('IMPORT_TIMEOUT','视频导入超时，请稍后重试。',408) from None
            if isinstance(error, Exception): raise media.InputError('IMPORT_FAILED','视频导入失败，请稍后重试。',503) from None
            raise

class URLInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    url:str=Field(min_length=1,max_length=4096)
    rights_confirmed:StrictBool

@router.post('/inputs/url',status_code=201)
async def import_url(data:URLInput,request:Request,user:Principal=Depends(mutation_user)):
    maintenance.ensure_open()
    if data.rights_confirmed is not True: raise media.InputError('RIGHTS_REQUIRED','请先确认拥有导入和处理授权。',400)
    safe_download.validate_url(data.url)
    identifier,key,path=begin_import(user)
    from app.task_locks import producer
    with producer('import-'+str(identifier)):
        try:
            await run_in_threadpool(safe_download.download_video,data.url,path,media.MAX_BYTES)
            return await validate_and_finish(request,user,identifier,key,path,'URL',None)
        except BaseException as error:
            cleanup_import(identifier,key,path,False)
            if isinstance(error, media.InputError): raise
            if isinstance(error, TimeoutError): raise media.InputError('IMPORT_TIMEOUT','视频导入超时，请稍后重试。',408) from None
            if isinstance(error, Exception): raise media.InputError('IMPORT_FAILED','视频导入失败，请稍后重试。',503) from None
            raise

async def input_error_handler(_request,error):
    return JSONResponse({'detail':error.detail,'code':error.code},status_code=error.status)
