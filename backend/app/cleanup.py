"""Task deletion, physically serialized verified cleanup and durable retries."""
from app import maintenance
from datetime import timedelta
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import json
import logging
import os
import random
import re
import time
from uuid import UUID
from fastapi import HTTPException
from botocore.exceptions import ClientError
from app import tasks,object_store,task_locks
logger=logging.getLogger(__name__)
_io_deadline=ContextVar('cleanup_io_deadline',default=None)
class BudgetExhausted(Exception):pass

class CleanupFailure(Exception):
    def __init__(self,kind,code='ASSET_CLEANUP_FAILED'):
        self.kind,self.code=kind,code
        super().__init__(code)

def _audit(c,t,actor,reason,result,now):
    c.execute("INSERT INTO audit_events(actor_user_id,target_user_id,task_id,event_type,reason_code,result,retention_class,created_at) VALUES(%s,%s,%s,'TASK_DELETION',%s,%s,'DELETION_90D',%s)",(actor,t['user_id'],t['id'],reason,result,now))

def _alert(c,j,code,now):
    inserted=c.execute("INSERT INTO cleanup_alerts(cleanup_job_id,task_id,user_id,alert_code,created_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",(j['id'],j['task_id'],j['user_id'],code,now)).fetchone()
    if inserted:logger.warning('cleanup_signal code=%s',code)

def _receipt(c,j):
    return {k:j[k] for k in ('id','task_id','status','started_at','online_completed_at','completed_at','job_version','attempt_count','consecutive_failures','next_attempt_at','blocked_reason','last_error_code')}

def _locks(c,job_id):
    found=c.execute('SELECT task_id,user_id FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone()
    if not found:return None,None
    tasks._user(c,found['user_id'])
    t=c.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(found['task_id'],)).fetchone()
    j=c.execute('SELECT * FROM cleanup_jobs WHERE id=%s FOR UPDATE',(job_id,)).fetchone()
    return t,j

def request_delete(task_id,user_id,reason='USER_DELETE',record=True,requested_at=None):
    return _request_delete(task_id,user_id,reason,record,requested_at)

def _request_delete(task_id,user_id,reason,record,requested_at,owner=None):
    if owner is None:maintenance.ensure_open()
    with (tasks.connect() if owner is None else nullcontext(owner)) as c:
        tasks._user(c,user_id)
        t=c.execute('SELECT * FROM tasks WHERE id=%s AND user_id=%s FOR UPDATE',(task_id,user_id)).fetchone()
        if not t:raise HTTPException(404,'Task not found')
        if owner is None:maintenance.ensure_open()
        now=requested_at or tasks.utcnow()
        if record:
            from app.deletion_control import record_intent
            record_intent(t['id'],t['user_id'],now)
        from app.legacy_support import close_for_task
        close_for_task(c,task_id,user_id if reason=='USER_DELETE' else None)
        j=c.execute('SELECT * FROM cleanup_jobs WHERE task_id=%s',(task_id,)).fetchone()
        replay=reason=='RESTORE_REPLAY'
        if j and t['deletion_requested_at'] is not None and not replay:
            result=_receipt(c,j)
            if owner is None:maintenance.ensure_open()
            return result
        c.execute("UPDATE tasks SET deletion_requested_at=COALESCE(deletion_requested_at,%s),status=CASE WHEN status IN('QUEUED','PROCESSING') THEN 'CANCELLED' ELSE status END,finished_at=CASE WHEN status IN('QUEUED','PROCESSING') THEN %s ELSE finished_at END,lease_generation=lease_generation+1,lease_owner=NULL,lease_expires_at=NULL,next_retry_at=NULL WHERE id=%s",(now,now,task_id))
        c.execute("UPDATE task_attempts SET status='CANCELLED',finished_at=%s,failure_category='USER_CANCELLED',failure_code='TASK_DELETED' WHERE task_id=%s AND status='PROCESSING'",(now,task_id))
        if j:
            c.execute("UPDATE media_assets SET cleanup_state='REGISTERED',deleted_at=NULL WHERE task_id=%s",(task_id,))
            j=c.execute("UPDATE cleanup_jobs SET status='PENDING',blocked_reason=NULL,completed_at=NULL,online_completed_at=NULL,next_attempt_at=%s,job_version=job_version+1 WHERE id=%s RETURNING *",(tasks.utcnow(),j['id'])).fetchone()
        else:j=c.execute('INSERT INTO cleanup_jobs(task_id,user_id,started_at,next_attempt_at) VALUES(%s,%s,%s,%s) RETURNING *',(task_id,user_id,now,tasks.utcnow())).fetchone()
        _audit(c,t,user_id if reason=='USER_DELETE' else None,reason,'REQUESTED',now)
        result=_receipt(c,j)
        if owner is None:maintenance.ensure_open()
        return result

def list_jobs(*args,**kwargs):raise HTTPException(410,'Deletion history retired')
def retry_job(*args,**kwargs):raise HTTPException(410,'Use controlled maintenance')

def _purge_bodies_and_logs(c,t):
    tid=t['id']
    c.execute('DELETE FROM support_requests WHERE task_id=%s',(tid,))
    c.execute('DELETE FROM task_results WHERE task_id=%s',(tid,))
    for table in ('model_calls','schedule_events','retry_events','quota_events','task_consents','task_attempts','evaluation_links'):
        c.execute('DELETE FROM '+table+' WHERE task_id=%s',(tid,))
    c.execute("DELETE FROM audit_events WHERE task_id=%s AND retention_class='TASK_30D'",(tid,))
    c.execute('UPDATE tasks SET display_filename=NULL,duration_seconds=NULL,failure_category=NULL,failure_code=NULL,retry_count=0,cooldown_level=0,cooldown_until=NULL,started_at=NULL,finished_at=NULL,last_heartbeat_at=NULL WHERE id=%s',(tid,))

def _progress(**values):
    from app.deletion_control import _write
    io='active_io_started' in values
    p=task_locks.runtime()/('cleanup-io.json' if io else 'cleanup-progress.json')
    if io:
        if values.get('active_io_started') is None:values['last_io_completed']=time.time()
        _write(p,values)
    else:
        old={}
        if p.exists():
            try:old=json.loads(p.read_text())
            except Exception:pass
        _write(p,dict(old,**values))

@contextmanager
def io_activity(identifier=None):
    # Only physical cleanup-global owners may enter this scope.
    existing=_io_deadline.get()
    token=_io_deadline.set(min(existing if existing is not None else float('inf'),time.monotonic()+30))
    _progress(active_io_started=time.time(),active_job_id=str(identifier) if identifier else None)
    try:yield
    finally:
        _progress(active_io_started=None,active_job_id=None)
        _io_deadline.reset(token)

def _rate():
    # All callers hold cleanup-global; retain pacing across processes/restarts.
    from app.deletion_control import _write
    p=task_locks.runtime()/'s3-rate.json'
    try:previous=float(json.loads(p.read_text())['at']) if p.exists() else 0
    except Exception:raise CleanupFailure('CONFIG','RATE_CONTROL_UNREADABLE')
    delay=.1-(time.time()-previous)
    if delay>0:time.sleep(delay)
    _write(p,{'at':time.time()})

def _s3call(client,method,**kw):
    maintenance.ensure_open()
    deadline=_io_deadline.get()
    if deadline is not None and time.monotonic()>=deadline:raise BudgetExhausted()
    _rate()
    if deadline is not None and time.monotonic()>=deadline:raise BudgetExhausted()
    result=getattr(client,method)(**kw)
    maintenance.ensure_open()
    return result

def _multipart(client,bucket,key):
    def uploads():
        marker={}
        while True:
            response=_s3call(client,'list_multipart_uploads',Bucket=bucket,Prefix=key,**marker)
            for upload in response.get('Uploads',[]):
                if upload.get('Key')==key:yield upload
            if not response.get('IsTruncated'):return
            next_marker={'KeyMarker':response.get('NextKeyMarker',''),'UploadIdMarker':response.get('NextUploadIdMarker','')}
            if next_marker==marker or not next_marker['KeyMarker']:raise CleanupFailure('CONFIG','MULTIPART_UNCONFIRMED')
            marker=next_marker
    for upload in uploads():
        try:_s3call(client,'abort_multipart_upload',Bucket=bucket,Key=key,UploadId=upload['UploadId'])
        except ClientError as exc:
            if str(exc.response['Error']['Code'])!='NoSuchUpload':raise
    if next(uploads(),None) is not None:raise CleanupFailure('TRANSIENT','MULTIPART_REMAINS')

def _delete_object(asset):
    bucket=asset['storage_bucket'];key=asset['storage_key'];version=asset['storage_version']
    if asset['storage_backend']!=object_store.backend_id() or not bucket or bucket!=object_store.bucket():raise CleanupFailure('CONFIG','BUCKET_UNCONFIRMED')
    if (not re.match(r'^(originals|derived|evaluation|evaluations|frames|audio|results|exports|cache|temp|annotations|failure-cases)/',key) and not(asset.get('asset_kind')=='BACKUP' and asset.get('cleanup_approved_at') and key.startswith('backups/'))) or '..' in key.split('/') or '\\' in key:raise CleanupFailure('UNSAFE','INVALID_ASSET_KEY')
    if key.startswith('originals/'):
        parts=key.split('/')
        try:valid=len(parts)==3 and UUID(parts[1])==asset['user_id'] and UUID(parts[2])
        except ValueError:valid=False
        if not valid:raise CleanupFailure('UNSAFE','INVALID_ASSET_KEY')
    client=object_store.cleanup_client()
    _s3call(client,'head_bucket',Bucket=bucket)
    config=_s3call(client,'get_bucket_versioning',Bucket=bucket)
    if config.get('Status') in ('Enabled','Suspended') or version:raise CleanupFailure('CONFIG','VERSION_UNCONFIRMED')
    _multipart(client,bucket,key)
    kw={'Bucket':bucket,'Key':key}
    if version:kw['VersionId']=version
    _s3call(client,'delete_object',**kw)
    try:_s3call(client,'head_object',**kw)
    except ClientError as exc:
        if str(exc.response['Error']['Code']) in ('404','NoSuchKey','NotFound','NoSuchVersion'):
            # Missing bucket is never a missing object, even if HEAD used 404.
            _s3call(client,'head_bucket',Bucket=bucket)
            return
        raise
    raise CleanupFailure('TRANSIENT','OBJECT_STILL_PRESENT')

def _physical(asset):
    maintenance.ensure_open()
    if asset['storage_backend'].startswith('WORKSPACE'):
        from app.processing import _workspace_path,_remove_workspace
        from app.processing import workspace_backend
        if asset['storage_backend']!=workspace_backend():raise CleanupFailure('CONFIG','WORKSPACE_UNCONFIRMED')
        path,_=_workspace_path(asset['storage_key'])
        if path.is_symlink():raise CleanupFailure('UNSAFE','WORKSPACE_LINK')
        _remove_workspace(path)
        if path.exists():raise CleanupFailure('TRANSIENT','WORKSPACE_REMAINS')
    elif asset['storage_backend'].startswith('S3'):_delete_object(asset)
    else:raise CleanupFailure('UNSAFE','UNKNOWN_BACKEND')

def _classify(exc):
    if isinstance(exc,CleanupFailure):return exc.kind,exc.code
    if isinstance(exc,(task_locks.LockUnsafe,ValueError)):return 'UNSAFE','CONTROL_UNSAFE'
    if isinstance(exc,ClientError):
        code=str(exc.response['Error']['Code'])
        if code in ('403','AccessDenied','NoSuchBucket','InvalidAccessKeyId','SignatureDoesNotMatch','404','NotImplemented','501','MethodNotAllowed','UnsupportedOperation'):return 'CONFIG','STORAGE_CONFIGURATION'
    return 'TRANSIENT','ASSET_CLEANUP_FAILED'

def _block(job_id,kind,code):
    with tasks.connect() as c:
        _,j=_locks(c,job_id)
        if not j:return None
        c.execute("UPDATE cleanup_jobs SET status='BLOCKED',blocked_reason=%s,last_error_kind=%s,last_error_code=%s,job_version=job_version+1 WHERE id=%s",(kind,kind,code,job_id))
        _alert(c,j,code,tasks.utcnow())
        result=_receipt(c,c.execute('SELECT * FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone())
        maintenance.ensure_open()
        return result

def process_job(job_id,manual=False):
    maintenance.ensure_open()
    with tasks.connect() as c:
        found=c.execute('SELECT task_id FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone()
        maintenance.ensure_open()
    if not found:return None
    # Fenced SQL bodies can be erased even while an old producer holds SH.
    # Release all SQL locks before attempting any physical lock.
    with tasks.connect() as c:
        task,job=_locks(c,job_id)
        if job and job['status']=='COMPLETED':return _receipt(c,job)
        if task and task['deletion_requested_at']:_purge_bodies_and_logs(c,task)
        maintenance.ensure_open()
    try:
        with task_locks.cleanup_global(),task_locks.exclusive(found['task_id']):
            return _process_locked(job_id)
    except task_locks.LockBusy:
        with tasks.connect() as c:
            _,j=_locks(c,job_id)
            if j and tasks.utcnow()-j['started_at']>timedelta(minutes=30):_alert(c,j,'CLEANUP_TIME_LIMIT',tasks.utcnow())
            result=_receipt(c,j) if j else None
            maintenance.ensure_open()
            return result
    except task_locks.LockUnsafe:return _block(job_id,'UNSAFE','CONTROL_UNSAFE')
    except CleanupFailure as exc:return _block(job_id,exc.kind,exc.code)

def _process_locked(job_id):
    token=_io_deadline.set(time.monotonic()+30)
    try:return _process_round(job_id)
    finally:_io_deadline.reset(token)

def _process_round(job_id):
    maintenance.ensure_open()
    now=tasks.utcnow()
    with tasks.connect() as c:
        t,j=_locks(c,job_id)
        if j['status'] in ('COMPLETED','BLOCKED'):return _receipt(c,j)
        if now-j['started_at']>timedelta(minutes=30):_alert(c,j,'CLEANUP_TIME_LIMIT',now)
        deferred=(j['next_attempt_at'] and now<j['next_attempt_at']) or (j['last_physical_started_at'] and now<j['last_physical_started_at']+timedelta(seconds=60))
        if deferred:
            result=_receipt(c,j)
            maintenance.ensure_open()
            return result
        if not t['deletion_requested_at']:raise CleanupFailure('UNSAFE','DELETE_INTENT_MISSING')
        # DB locators must be checked before removing their source rows.
        for a in c.execute("SELECT * FROM media_assets WHERE task_id=%s AND storage_backend='DB' AND cleanup_state<>'DELETED'",(t['id'],)).fetchall():
            m=re.fullmatch(r'db-result/([0-9a-f-]{36})/(export|presentation)',a['storage_key'])
            if not m:raise CleanupFailure('UNSAFE','INVALID_ASSET_KEY')
            result=c.execute('SELECT task_id,user_id FROM task_results WHERE id=%s',(UUID(m[1]),)).fetchone()
            if result and(result['task_id']!=t['id'] or result['user_id']!=t['user_id']):raise CleanupFailure('UNSAFE','RESULT_OWNER_MISMATCH')
            c.execute("UPDATE media_assets SET cleanup_state='DELETED',deleted_at=%s WHERE id=%s",(now,a['id']))
        _purge_bodies_and_logs(c,t)
        maintenance.ensure_open()
    if not task_locks.producer_ready():return _block(job_id,'PRODUCER_UNKNOWN','PRODUCER_UNCONFIRMED')
    with tasks.connect() as c:
        t,j=_locks(c,job_id)
        version=c.execute('UPDATE cleanup_jobs SET attempt_count=attempt_count+1,last_physical_started_at=%s,job_version=job_version+1 WHERE id=%s RETURNING job_version',(now,job_id)).fetchone()['job_version']
        assets=c.execute("SELECT * FROM media_assets WHERE task_id=%s AND cleanup_state<>'DELETED' ORDER BY (asset_kind='BACKUP'),created_at,id",(t['id'],)).fetchall()
        maintenance.ensure_open()
    failure=None;count=0;deadline=time.monotonic()+30
    for a in assets:
        if a['asset_kind']=='BACKUP' and not a['cleanup_approved_at']:continue
        if count>=20 or time.monotonic()>=deadline:break
        count+=1
        _progress(active_io_started=time.time(),active_job_id=str(job_id))
        try:_physical(a)
        except BudgetExhausted:break
        except Exception as exc:
            failure=_classify(exc)
            if failure[0]!='TRANSIENT':break
        else:
            with tasks.connect() as c:
                _,fresh=_locks(c,job_id)
                if fresh['job_version']!=version:return _receipt(c,fresh)
                current=c.execute('SELECT * FROM media_assets WHERE id=%s',(a['id'],)).fetchone()
                if not current or any(current[k]!=a[k] for k in ('task_id','user_id','storage_key','storage_backend','storage_bucket','storage_version')):raise CleanupFailure('UNSAFE','ASSET_CHANGED')
                c.execute("UPDATE media_assets SET cleanup_state='DELETED',deleted_at=%s WHERE id=%s",(tasks.utcnow(),a['id']))
                maintenance.ensure_open()
        finally:_progress(active_io_started=None,active_job_id=None)
    with tasks.connect() as c:
        t,j=_locks(c,job_id)
        maintenance.ensure_open()
        if j['job_version']!=version:return _receipt(c,j)
        now=tasks.utcnow()
        remaining=c.execute("SELECT asset_kind,cleanup_approved_at FROM media_assets WHERE task_id=%s AND cleanup_state<>'DELETED'",(t['id'],)).fetchall()
        online=not any(a['asset_kind']!='BACKUP' for a in remaining)
        if online and j['online_completed_at'] is None:
            c.execute('UPDATE cleanup_jobs SET online_completed_at=%s,job_version=job_version+1 WHERE id=%s',(now,job_id))
        if failure:
            kind,code=failure
            if kind=='TRANSIENT':
                n=j['consecutive_failures']+1;wait=min(3600,60*2**min(n-1,6)*(1+random.uniform(0,.2)))
                c.execute("UPDATE cleanup_jobs SET status='RETRY_WAIT',blocked_reason=NULL,consecutive_failures=%s,next_attempt_at=%s,last_error_kind=%s,last_error_code=%s,job_version=job_version+1 WHERE id=%s",(n,now+timedelta(seconds=wait),kind,code,job_id))
                if n>=5:_alert(c,j,'CLEANUP_RETRY_LIMIT',now)
            else:
                c.execute("UPDATE cleanup_jobs SET status='BLOCKED',blocked_reason=%s,last_error_kind=%s,last_error_code=%s,job_version=job_version+1 WHERE id=%s",(kind,kind,code,job_id));_alert(c,j,code,now)
        elif not remaining:
            # Retain only opaque control locators for the bounded restore window.
            for locator in c.execute('SELECT id,storage_key FROM media_assets WHERE task_id=%s',(t['id'],)).fetchall():
                key=locator['storage_key']
                opaque=(re.fullmatch(r'originals/[0-9a-f-]{36}/[0-9a-f-]{36}',key)
                        or re.fullmatch(r'local-workspace/[0-9]+/[0-9a-f]{32}',key)
                        or re.fullmatch(r'db-result/[0-9a-f-]{36}/(export|presentation)',key))
                if not opaque:
                    c.execute('DELETE FROM evaluation_links WHERE asset_id=%s',(locator['id'],))
                    c.execute('DELETE FROM media_assets WHERE id=%s',(locator['id'],))
            c.execute("UPDATE cleanup_jobs SET status='COMPLETED',blocked_reason=NULL,online_completed_at=COALESCE(online_completed_at,%s),completed_at=%s,next_attempt_at=NULL,consecutive_failures=0,last_error_kind=NULL,last_error_code=NULL,job_version=job_version+1 WHERE id=%s",(now,now,job_id))
            c.execute('UPDATE tasks SET deleted_at=COALESCE(deleted_at,%s) WHERE id=%s',(now,t['id']))
            c.execute('UPDATE cleanup_alerts SET resolved_at=%s WHERE cleanup_job_id=%s AND resolved_at IS NULL',(now,job_id))
            _audit(c,t,None,'CLEANUP','COMPLETED',now)
        elif online and all(not a['cleanup_approved_at'] for a in remaining):
            c.execute("UPDATE cleanup_jobs SET status='BLOCKED',blocked_reason='LEGACY_APPROVAL',online_completed_at=COALESCE(online_completed_at,%s),consecutive_failures=0,job_version=job_version+1 WHERE id=%s",(now,job_id));_alert(c,j,'LEGACY_APPROVAL',now)
        else:
            c.execute("UPDATE cleanup_jobs SET status='PENDING',blocked_reason=NULL,next_attempt_at=%s,job_version=job_version+1 WHERE id=%s",(now+timedelta(seconds=60),job_id))
        result=_receipt(c,c.execute('SELECT * FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone())
        maintenance.ensure_open()
        return result

def run_once():
    maintenance.ensure_open()
    from app.deletion_control import replay_journal
    replay_journal(force=False)
    _progress(loop_started=time.time())
    now=tasks.utcnow()
    try:
        with task_locks.cleanup_global():
            _progress(active_io_started=None,active_job_id=None)
    except task_locks.LockBusy:pass
    with tasks.connect() as c:
        expired=c.execute('SELECT id,user_id FROM tasks WHERE created_at<=%s AND deletion_requested_at IS NULL',(now-timedelta(days=30),)).fetchall()
        maintenance.ensure_open()
    for t in expired:request_delete(t['id'],t['user_id'],reason='RETENTION_EXPIRED')
    # Physical safety blocks never extend retention of independently fenced SQL.
    with tasks.connect() as c:
        blocked=c.execute("SELECT j.id FROM cleanup_jobs j JOIN tasks t ON(t.id,t.user_id)=(j.task_id,j.user_id) WHERE t.deletion_requested_at IS NOT NULL AND(j.status='BLOCKED' OR t.created_at<=%s)",(now-timedelta(days=30),)).fetchall()
        maintenance.ensure_open()
    for row in blocked:
        with tasks.connect() as c:
            task,job=_locks(c,row['id'])
            if task and job and task['deletion_requested_at'] is not None and (job['status']=='BLOCKED' or task['created_at']<=now-timedelta(days=30)):
                _purge_bodies_and_logs(c,task)
            maintenance.ensure_open()
    with tasks.connect() as c:due=c.execute("SELECT id FROM cleanup_jobs WHERE status IN('PENDING','RETRY_WAIT') AND(next_attempt_at IS NULL OR next_attempt_at<=%s) ORDER BY started_at,id",(now,)).fetchall()
    out=[]
    for j in due:
        try:out.append(process_job(j['id']))
        except CleanupFailure as exc:out.append(_block(j['id'],exc.kind,exc.code))
    with tasks.connect() as c:
        for j in c.execute("SELECT * FROM cleanup_jobs WHERE status<>'COMPLETED' AND started_at<%s",(now-timedelta(minutes=30),)).fetchall():_alert(c,j,'CLEANUP_TIME_LIMIT',now)
        c.execute("DELETE FROM audit_events WHERE retention_class='DELETION_90D' AND created_at<=%s",(now-timedelta(days=90),))
        c.execute("DELETE FROM audit_events a WHERE retention_class='TASK_30D' AND ((task_id IS NULL AND created_at<=%s) OR EXISTS(SELECT 1 FROM tasks t WHERE t.id=a.task_id AND t.created_at<=%s))",(now-timedelta(days=30),now-timedelta(days=30)))
        maintenance.ensure_open()
    from app.deletion_control import prune_journal,_root,identity_ready
    prune_journal(now)
    with tasks.connect() as c:
        old=c.execute("SELECT * FROM cleanup_jobs WHERE status='COMPLETED' AND completed_at<=%s",(now-timedelta(days=90),)).fetchall()
        for j in old:
            if not identity_ready() or not task_locks.producer_ready():continue
            journal=_root()
            if journal and(journal/(str(j['task_id'])+'.json')).exists():continue
            _,fresh=_locks(c,j['id'])
            if not fresh or fresh['job_version']!=j['job_version'] or fresh['status']!='COMPLETED':continue
            if c.execute("SELECT 1 FROM media_assets WHERE task_id=%s AND cleanup_state<>'DELETED' LIMIT 1",(j['task_id'],)).fetchone():continue
            c.execute('DELETE FROM evaluation_links WHERE task_id=%s',(j['task_id'],))
            c.execute('DELETE FROM media_assets WHERE task_id=%s',(j['task_id'],))
            c.execute('DELETE FROM cleanup_alerts WHERE cleanup_job_id=%s',(j['id'],))
            c.execute('DELETE FROM cleanup_jobs WHERE id=%s',(j['id'],))
            c.execute('DELETE FROM tasks WHERE id=%s',(j['task_id'],))
        maintenance.ensure_open()
    _progress(last_loop_completed=time.time())
    return out
