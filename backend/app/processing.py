"""Private media -> model candidate. Final output validation belongs to S3-08."""
from app import maintenance
from contextlib import contextmanager, ExitStack
from pathlib import Path
import logging
import os
import re
import shutil
import time
from uuid import uuid4
from app import object_store, tasks
from app.preprocess import prepare_media, PreprocessError
from app.media import MAX_BYTES
from app.model_adapter import ModelRequest, VisualEvidence, AudioEvidence, ModelResponse, TokenUsage, AdapterError, validate_response, validate_usage

logger = logging.getLogger(__name__)

class ProcessingCancelled(Exception):
    pass

class AlreadyAnalyzed(AdapterError):
    """This attempt already owns a call; never re-execute or change task state."""
    def __init__(self):super().__init__('internal')

def _check(context):
    maintenance.ensure_open()
    if context.cancelled.is_set() or context.checkpoint() is False:
        raise ProcessingCancelled('Processing cancelled')
    return True

def _root():
    root=Path(os.getenv('PROCESSING_WORK_DIR','/var/lib/video-summary-app/processing'))
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if root.is_symlink():raise ValueError('Unsafe workspace root')
    return root.resolve()

def workspace_backend():
    import hashlib
    root=_root();st=root.stat()
    return 'WORKSPACE:'+hashlib.sha256((str(root)+':'+str(st.st_dev)+':'+str(st.st_ino)).encode()).hexdigest()

def _workspace_path(key):
    match=re.fullmatch(r'local-workspace/([0-9]+)/([0-9a-f]{32})',key)
    if not match:raise ValueError('Invalid workspace key')
    return _root()/match.group(2),int(match.group(1))

def _remove_workspace(path):
    maintenance.ensure_open()
    if path.is_symlink():raise ValueError('Unsafe workspace link')
    elif path.exists():
        for directory,dirs,files in os.walk(path,topdown=False,followlinks=False):
            for name in files:
                maintenance.ensure_open();(Path(directory)/name).unlink()
            for name in dirs:
                maintenance.ensure_open();child=Path(directory)/name
                if child.is_symlink():child.unlink()
                else:child.rmdir()
        maintenance.ensure_open();path.rmdir()

def _clean_registered(asset_id,key):
    if not maintenance.ready():return False # Registered locator remains for later recovery.
    try:
        path,_=_workspace_path(key);_remove_workspace(path)
        with tasks.connect() as c:
            c.execute("UPDATE media_assets SET cleanup_state='DELETED',deleted_at=now() WHERE id=%s AND storage_key=%s AND asset_kind='PROCESSING_WORKSPACE'",(asset_id,key))
        return True
    except Exception:
        try:
            with tasks.connect() as c:c.execute("UPDATE media_assets SET cleanup_state='FAILED' WHERE id=%s AND storage_key=%s",(asset_id,key))
        except Exception:pass
        return False

def recover_workspaces():
    maintenance.ensure_open()
    from app import task_locks
    from app.cleanup import io_activity
    if not task_locks.producer_ready():return []
    with tasks.connect() as c:
        rows=c.execute("SELECT * FROM media_assets WHERE asset_kind IN ('PROCESSING_WORKSPACE','QWEN_TRANSFER') AND cleanup_state<>'DELETED'").fetchall()
        maintenance.ensure_open()
    recovered=[]
    for row in rows:
        try:
            with task_locks.cleanup_global(),task_locks.exclusive(row['task_id']),io_activity(row['id']):
                with tasks.connect() as c:
                    task=c.execute('SELECT * FROM tasks WHERE id=%s',(row['task_id'],)).fetchone()
                    if not task or task['user_id']!=row['user_id']:continue
                    if task['status']=='PROCESSING' and not task['deletion_requested_at'] and not task['deleted_at']:continue
                    maintenance.ensure_open()
                if row['asset_kind'] == 'QWEN_TRANSFER':
                    cleaned = _clean_cloud_asset(row)
                else:
                    if row['storage_backend']!=workspace_backend():continue
                    cleaned = _clean_registered(row['id'],row['storage_key'])
                if cleaned:recovered.append(str(row['id']))
        except task_locks.LockBusy:continue
    return recovered


def _clean_cloud_asset(asset, cloud=None):
    if not maintenance.ready():
        return False
    from app.qwen_cloud import CloudVideoStore
    try:
        store = cloud or CloudVideoStore.from_environment()
        if store is None:
            raise ValueError('Missing OSS configuration')
        store.remove(asset)
        maintenance.ensure_open()
        with tasks.connect() as connection:
            connection.execute("UPDATE media_assets SET cleanup_state='DELETED',deleted_at=now() WHERE id=%s",
                               (asset['id'],))
        return True
    except Exception:
        # Keep the opaque locator for the next recovery pass.
        logger.warning('Temporary Qwen video cleanup pending for asset %s', asset['id'])
        return False


@contextmanager
def _cloud_video(context, cloud, path, filename):
    try:
        cloud.check_bucket()
    except Exception:
        raise AdapterError('cloud_transfer') from None

    def register(connection, task):
        key = cloud.asset_key(task['id'], filename)
        return connection.execute(
            """INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key,storage_backend,storage_bucket,byte_size)
               VALUES (%s,%s,'QWEN_TRANSFER',%s,%s,%s,%s) RETURNING *""",
            (task['id'], task['user_id'], key, cloud.backend, cloud.bucket, path.stat().st_size),
        ).fetchone()

    asset = context.fenced_write(register)
    try:
        try:
            url = cloud.upload(path, asset['storage_key'], lambda: _check(context))
        except Exception:
            _check(context)
            raise AdapterError('cloud_transfer') from None
        yield url
    finally:
        _clean_cloud_asset(asset, cloud)

@contextmanager
def _workspace(context):
    identifier=uuid4().hex;key=f'local-workspace/{context.generation}/{identifier}'
    def register(c,task):
        return c.execute("INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key,storage_backend) VALUES (%s,%s,'PROCESSING_WORKSPACE',%s,%s) RETURNING id",(task['id'],task['user_id'],key,workspace_backend())).fetchone()['id']
    asset=context.fenced_write(register);path,_=_workspace_path(key)
    try:
        # Directory creation shares the task fence, so a paused old Worker
        # cannot recreate its registered workspace after deletion.
        context.fenced_write(lambda c,task: path.mkdir(mode=0o700))
        yield path
    finally:
        if maintenance.ready() and not _clean_registered(asset,key):raise AdapterError('internal')

def download_original(key,path,checkpoint,*,inline_limit=None):
    maintenance.ensure_open()
    response=object_store.client().get_object(Bucket=object_store.bucket(),Key=key)
    body=response['Body'];expected=response.get('ContentLength');total=0;deadline=time.monotonic()+1800
    try:
        if type(expected) is not int or not 0<expected<=MAX_BYTES:raise AdapterError('internal')
        if inline_limit is not None and expected>inline_limit:raise AdapterError('inline_video_too_large')
        limit = MAX_BYTES if inline_limit is None else inline_limit
        with path.open('xb') as target:
            while True:
                checkpoint()
                if time.monotonic()>deadline:raise AdapterError('internal')
                chunk=body.read(min(1024*1024,limit-total+1))
                maintenance.ensure_open()
                if not chunk:break
                total+=len(chunk)
                if total>limit:raise AdapterError('internal')
                target.write(chunk)
        if total!=expected:raise AdapterError('internal')
    finally:body.close()

def _tag(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value):raise AdapterError('invalid_response')
    return value

def _record_finish(context,status,response=None,error=None):
    maintenance.ensure_open()
    usage=response.usage if response is not None else error.usage if error is not None else TokenUsage()
    request_id=response.request_id if response is not None else error.request_id if error is not None else None
    try:
        validate_usage(usage)
        if request_id is not None:request_id=_tag(request_id)
    except AdapterError:
        usage=TokenUsage();request_id=None
    def write(c,task):
        c.execute('''UPDATE model_calls SET status=%s,request_id=%s,input_tokens=%s,output_tokens=%s,cached_tokens=%s,estimated_cost=%s,currency=%s,model_version=COALESCE(%s,model_version),adapter_version=COALESCE(%s,adapter_version),error_category=%s,finished_at=now() WHERE task_id=%s AND lease_generation=%s''',(status,request_id,usage.input_tokens,usage.output_tokens,usage.cached_tokens,usage.estimated_cost,usage.currency,response.model_version if response else None,response.adapter_version if response else None,error.failure_category if error else None,task['id'],context.generation))
    context.fenced_write(write)


def _read_frame(context,path):
    _check(context)
    data=path.read_bytes()
    _check(context)
    return data

class _AudioReader:
    """An owned audio stream that checks the maintenance gate around file I/O."""
    def __init__(self,context,path):
        self.context=context
        self._check()
        self.stream=path.open('rb')
        try:self._check()
        except BaseException:self.stream.close();raise
    def _check(self):
        # The adapter checkpoint and Worker heartbeat already check the lease.
        maintenance.ensure_open()
        if self.context.cancelled.is_set():
            raise ProcessingCancelled('Processing cancelled')
    def read(self,size=-1):
        try:
            self._check()
            data=self.stream.read(size)
            self._check()
            return data
        except BaseException:
            self.close()
            raise
    def close(self):self.stream.close()
    @property
    def closed(self):return self.stream.closed
    def __enter__(self):
        try:self._check();return self
        except BaseException:self.close();raise
    def __exit__(self,*args):self.close()

def _prepare_and_analyze(context,adapter,language='zh') -> ModelResponse:
    """One adapter attempt; never invokes complete or automatically retries."""
    maintenance.ensure_open()
    try:
        _check(context)
        def source(c,task):
            if c.execute('SELECT id FROM model_calls WHERE task_id=%s AND lease_generation=%s',(task['id'],context.generation)).fetchone():raise AlreadyAnalyzed()
            row=c.execute("SELECT storage_key FROM media_assets WHERE task_id=%s AND user_id=%s AND asset_kind='ORIGINAL' AND cleanup_state='REGISTERED' ORDER BY created_at,id LIMIT 1",(task['id'],task['user_id'])).fetchone()
            if row is None:raise AdapterError('internal')
            return row['storage_key'],task.get('duration_seconds'),task.get('display_filename')
        key,duration,filename=context.fenced_write(source)
        with _workspace(context) as directory, ExitStack() as transfers:
            original=directory/'original'
            timeout=float(os.getenv('MODEL_ADAPTER_TIMEOUT_SECONDS','120'))
            inline_limit=getattr(adapter,'max_inline_video_bytes',None)
            if inline_limit is not None:
                if duration is None:raise AdapterError('input')
                cloud = getattr(adapter, 'cloud', None)
                download_original(key,original,lambda:_check(context),inline_limit=None if cloud else inline_limit)
                use_cloud = original.stat().st_size > inline_limit
                url = transfers.enter_context(_cloud_video(context, cloud, original, filename)) if use_cloud else None
                request=ModelRequest(float(duration),(),language=language,timeout_seconds=timeout,
                    prompt_version=adapter.prompt_version,
                    open_video=None if use_cloud else lambda:_AudioReader(context,original),video_url=url,
                    task_id=str(context.id))
                policy_version='qwen-oss-video-v1' if use_cloud else 'qwen-inline-video-v1'
            else:
                download_original(key,original,lambda:_check(context))
                prepared=prepare_media(original,directory,checkpoint=lambda:_check(context))
                request=ModelRequest(prepared.duration_seconds,tuple(VisualEvidence(f.timestamp_seconds,_read_frame(context,f.path),track_index=getattr(f,'track_index',0)) for f in prepared.frames),tuple(AudioEvidence(a.start_seconds,a.end_seconds,lambda p=a.path:_AudioReader(context,p),track_index=getattr(a,'track_index',i)) for i,a in enumerate(prepared.audio_tracks)),tuple(prepared.limitations),language=language,timeout_seconds=timeout)
                policy_version=prepared.policy_version
            if not 0<request.timeout_seconds<=3600:raise AdapterError('internal')
            model=_tag(adapter.model_version);version=_tag(adapter.adapter_version)
            def begin(c,task):
                attempt=c.execute('SELECT id FROM task_attempts WHERE task_id=%s AND lease_generation=%s',(task['id'],context.generation)).fetchone()
                row=c.execute('''INSERT INTO model_calls(task_id,user_id,attempt_id,lease_generation,status,model_version,adapter_version,prompt_version,policy_version) VALUES (%s,%s,%s,%s,'RUNNING',%s,%s,%s,%s) ON CONFLICT (task_id,lease_generation) DO NOTHING RETURNING id''',(task['id'],task['user_id'],attempt['id'],context.generation,model,version,request.prompt_version,policy_version)).fetchone()
                if row is None:raise AlreadyAnalyzed()
            context.fenced_write(begin);deadline=time.monotonic()+request.timeout_seconds
            def checkpoint():
                _check(context)
                if time.monotonic()>deadline:raise AdapterError('timeout')
                return True
            try:
                response=adapter.analyze(request,checkpoint=checkpoint)
                _check(context)
                if time.monotonic()>deadline:
                    try:
                        usage=validate_usage(response.usage);request_id=_tag(response.request_id)
                    except (AdapterError,AttributeError):usage=TokenUsage();request_id=None
                    raise AdapterError('timeout',request_id=request_id,usage=usage)
                validate_response(response,request.duration_seconds)
                _tag(response.request_id);_tag(response.model_version);_tag(response.adapter_version)
                visual={f.track_index for f in request.frames};audio={a.track_index for a in request.audio_tracks}
                for observation in response.observations:
                    if observation.modality=='audio' and observation.track_index not in audio:raise AdapterError('invalid_response')
                    if observation.modality=='visual' and observation.track_index not in visual:raise AdapterError('invalid_response')
                _record_finish(context,'SUCCEEDED',response=response);return response
            except AdapterError as error:
                _record_finish(context,'FAILED',error=error);raise
            except ProcessingCancelled:raise
            except Exception:
                error=AdapterError('internal');_record_finish(context,'FAILED',error=error);raise error from None
    except ProcessingCancelled:raise
    except PreprocessError as error:
        if error.code=='PREPROCESS_CANCELLED':raise ProcessingCancelled('Processing cancelled') from None
        mapped=AdapterError('internal')
        context.fail(mapped.failure_category,'PREPROCESS_FAILED');raise mapped from None
    except AlreadyAnalyzed:raise
    except AdapterError as error:
        if error.kind=='cancelled':raise ProcessingCancelled('Processing cancelled') from None
        context.fail(error.failure_category,'MODEL_'+error.kind.upper());raise
    except Exception:
        context.fail('INTERNAL','PROCESSING_FAILED');raise AdapterError('internal') from None


def prepare_and_analyze(context,adapter,language='zh') -> ModelResponse:
    from app.task_locks import producer
    with producer(context.id):
        return _prepare_and_analyze(context,adapter,language)
