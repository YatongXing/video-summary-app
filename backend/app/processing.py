"""Private media -> model candidate. Final output validation belongs to S3-08."""
from contextlib import contextmanager
from pathlib import Path
import os
import re
import shutil
import time
from uuid import uuid4
from app import object_store, tasks
from app.preprocess import prepare_media, PreprocessError
from app.media import MAX_BYTES
from app.model_adapter import ModelRequest, VisualEvidence, AudioEvidence, ModelResponse, TokenUsage, AdapterError, validate_response, validate_usage

class ProcessingCancelled(Exception):
    pass

class AlreadyAnalyzed(AdapterError):
    """This attempt already owns a call; never re-execute or change task state."""
    def __init__(self):super().__init__('internal')

def _check(context):
    if context.cancelled.is_set() or context.checkpoint() is False:
        raise ProcessingCancelled('Processing cancelled')
    return True

def _root():
    root=Path(os.getenv('PROCESSING_WORK_DIR','/var/lib/video-summary-app/processing'))
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    return root.resolve()

def _workspace_path(key):
    match=re.fullmatch(r'local-workspace/([0-9]+)/([0-9a-f]{32})',key)
    if not match:raise ValueError('Invalid workspace key')
    return _root()/match.group(2),int(match.group(1))

def _remove_workspace(path):
    if path.is_symlink():path.unlink()
    elif path.exists():shutil.rmtree(path)

def _clean_registered(asset_id,key):
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
    """Explicit recovery: never removes live current-generation work."""
    with tasks.connect() as c:
        rows=c.execute("SELECT id,task_id,storage_key FROM media_assets WHERE asset_kind='PROCESSING_WORKSPACE' AND cleanup_state<>'DELETED'").fetchall()
    recovered=[]
    for row in rows:
        try:
            path,generation=_workspace_path(row['storage_key'])
            with tasks.connect() as c:
                task=c.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(row['task_id'],)).fetchone()
                if task and task['status']=='PROCESSING' and task['lease_generation']==generation and task['lease_expires_at'] and task['lease_expires_at']>tasks.utcnow() and not task['deletion_requested_at'] and not task['deleted_at']:continue
                _remove_workspace(path)
                c.execute("UPDATE media_assets SET cleanup_state='DELETED',deleted_at=now() WHERE id=%s",(row['id'],))
                recovered.append(str(row['id']))
        except Exception:
            with tasks.connect() as c:c.execute("UPDATE media_assets SET cleanup_state='FAILED' WHERE id=%s",(row['id'],))
    return recovered

@contextmanager
def _workspace(context):
    identifier=uuid4().hex;key=f'local-workspace/{context.generation}/{identifier}'
    def register(c,task):
        return c.execute("INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key) VALUES (%s,%s,'PROCESSING_WORKSPACE',%s) RETURNING id",(task['id'],task['user_id'],key)).fetchone()['id']
    asset=context.fenced_write(register);path,_=_workspace_path(key)
    try:
        # Directory creation shares the task fence, so a paused old Worker
        # cannot recreate its registered workspace after deletion.
        context.fenced_write(lambda c,task: path.mkdir(mode=0o700))
        yield path
    finally:
        if not _clean_registered(asset,key):raise AdapterError('internal')

def download_original(key,path,checkpoint):
    response=object_store.client().get_object(Bucket=object_store.bucket(),Key=key)
    body=response['Body'];expected=response.get('ContentLength');total=0;deadline=time.monotonic()+1800
    try:
        if type(expected) is not int or not 0<expected<=MAX_BYTES:raise AdapterError('internal')
        with path.open('xb') as target:
            while True:
                checkpoint()
                if time.monotonic()>deadline:raise AdapterError('internal')
                chunk=body.read(min(1024*1024,MAX_BYTES-total+1))
                if not chunk:break
                total+=len(chunk)
                if total>MAX_BYTES:raise AdapterError('internal')
                target.write(chunk)
        if total!=expected:raise AdapterError('internal')
    finally:body.close()

def _tag(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value):raise AdapterError('invalid_response')
    return value

def _record_finish(context,status,response=None,error=None):
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

def prepare_and_analyze(context,adapter,language='zh') -> ModelResponse:
    """One adapter attempt; never invokes complete or automatically retries."""
    try:
        _check(context)
        def source(c,task):
            if c.execute('SELECT id FROM model_calls WHERE task_id=%s AND lease_generation=%s',(task['id'],context.generation)).fetchone():raise AlreadyAnalyzed()
            row=c.execute("SELECT storage_key FROM media_assets WHERE task_id=%s AND user_id=%s AND asset_kind='ORIGINAL' AND cleanup_state='REGISTERED' ORDER BY created_at,id LIMIT 1",(task['id'],task['user_id'])).fetchone()
            if row is None:raise AdapterError('internal')
            return row['storage_key']
        key=context.fenced_write(source)
        with _workspace(context) as directory:
            original=directory/'original';download_original(key,original,lambda:_check(context))
            prepared=prepare_media(original,directory,checkpoint=lambda:_check(context))
            request=ModelRequest(prepared.duration_seconds,tuple(VisualEvidence(f.timestamp_seconds,f.path.read_bytes(),track_index=getattr(f,'track_index',0)) for f in prepared.frames),tuple(AudioEvidence(a.start_seconds,a.end_seconds,lambda p=a.path:p.open('rb'),track_index=getattr(a,'track_index',i)) for i,a in enumerate(prepared.audio_tracks)),tuple(prepared.limitations),language=language,timeout_seconds=float(os.getenv('MODEL_ADAPTER_TIMEOUT_SECONDS','120')))
            if not 0<request.timeout_seconds<=3600:raise AdapterError('internal')
            model=_tag(adapter.model_version);version=_tag(adapter.adapter_version)
            def begin(c,task):
                attempt=c.execute('SELECT id FROM task_attempts WHERE task_id=%s AND lease_generation=%s',(task['id'],context.generation)).fetchone()
                row=c.execute('''INSERT INTO model_calls(task_id,user_id,attempt_id,lease_generation,status,model_version,adapter_version,prompt_version,policy_version) VALUES (%s,%s,%s,%s,'RUNNING',%s,%s,%s,%s) ON CONFLICT (task_id,lease_generation) DO NOTHING RETURNING id''',(task['id'],task['user_id'],attempt['id'],context.generation,model,version,request.prompt_version,prepared.policy_version)).fetchone()
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
