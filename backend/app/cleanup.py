"""Task-linked deletion, bounded retries and retention. No content in receipts."""
from datetime import timedelta
import os
import re
from uuid import UUID
from fastapi import HTTPException
from botocore.exceptions import ClientError
from app import tasks, object_store

class CleanupFailure(Exception):
    def __init__(self,kind,code='ASSET_CLEANUP_FAILED'):
        self.kind=kind;self.code=code
        super().__init__(code)

def _audit(c,t,actor,reason,result,now):
    c.execute("INSERT INTO audit_events(actor_user_id,target_user_id,task_id,event_type,reason_code,result,retention_class,created_at) VALUES (%s,%s,%s,'TASK_DELETION',%s,%s,'DELETION_90D',%s)",(actor,t['user_id'],t['id'],reason,result,now))

def _alert(c,job,code,now):
    c.execute("INSERT INTO cleanup_alerts(cleanup_job_id,task_id,user_id,alert_code,created_at) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",(job['id'],job['task_id'],job['user_id'],code,now))

def _receipt(c,row):
    result=dict(row)
    result['online_complete']=row['status']=='COMPLETED'
    result['backup_verified']=row['backup_cleanup_state'] in ('COMPLETED','NOT_APPLICABLE')
    result['alerts']=c.execute('SELECT id,alert_code,created_at,resolved_at FROM cleanup_alerts WHERE cleanup_job_id=%s ORDER BY created_at,id',(row['id'],)).fetchall()
    return result

def request_delete(task_id,user_id,reason='USER_DELETE',record=True,requested_at=None):
    with tasks.connect() as c:
        u=tasks._user(c,user_id)
        t=c.execute('SELECT * FROM tasks WHERE id=%s AND user_id=%s FOR UPDATE',(task_id,user_id)).fetchone()
        if t is None:raise HTTPException(404,'Task not found')
        now=requested_at or tasks.utcnow()
        # Persist the restoration-independent intent before acknowledging a fence.
        if record:
            from app.deletion_control import record_intent
            record_intent(t['id'],t['user_id'],now)
        existing=c.execute('SELECT * FROM cleanup_jobs WHERE task_id=%s',(task_id,)).fetchone()
        replay=reason=='RESTORE_REPLAY'
        if existing and t['deletion_requested_at'] is not None and not replay:return _receipt(c,existing)
        safe_after=t['lease_expires_at'] if t['status']=='PROCESSING' else None
        c.execute("UPDATE tasks SET deletion_requested_at=COALESCE(deletion_requested_at,%s),status=CASE WHEN status IN ('QUEUED','PROCESSING') THEN 'CANCELLED' ELSE status END,finished_at=CASE WHEN status IN ('QUEUED','PROCESSING') THEN %s ELSE finished_at END,lease_generation=lease_generation+1,lease_owner=NULL,lease_expires_at=NULL,next_retry_at=NULL WHERE id=%s",(now,now,task_id))
        c.execute("UPDATE task_attempts SET status='CANCELLED',finished_at=%s,failure_category='USER_CANCELLED',failure_code='TASK_DELETED' WHERE task_id=%s AND status='PROCESSING'",(now,task_id))
        if existing:
            # A restored database or object snapshot is not evidence of cleanup.
            c.execute("UPDATE media_assets SET cleanup_state='REGISTERED',deleted_at=NULL WHERE task_id=%s",(task_id,))
            row=c.execute("UPDATE cleanup_jobs SET status='PENDING',completed_at=NULL,next_attempt_at=%s,worker_safe_after=%s,manual_requested=true,backup_cleanup_state='PENDING',backup_completed_at=NULL WHERE id=%s RETURNING *",(tasks.utcnow(),safe_after,existing['id'])).fetchone()
        else:
            row=c.execute('INSERT INTO cleanup_jobs(task_id,user_id,started_at,next_attempt_at,worker_safe_after,manual_requested) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',(task_id,user_id,now,tasks.utcnow(),safe_after,replay)).fetchone()
        _audit(c,t,user_id if reason=='USER_DELETE' else None,reason,'REQUESTED',now)
        return _receipt(c,row)

def list_jobs(user_id=None,limit=50,offset=0):
    with tasks.connect() as c:
        rows=c.execute('SELECT * FROM cleanup_jobs WHERE (%s::uuid IS NULL OR user_id=%s) ORDER BY started_at DESC,id LIMIT %s OFFSET %s',(user_id,user_id,limit,offset)).fetchall()
        return [_receipt(c,row) for row in rows]

def retry_job(job_id,admin_id):
    with tasks.connect() as c:
        admin=tasks._user(c,admin_id)
        if admin['role']!='ADMIN' or admin['disabled_at']:raise HTTPException(403,'Administrator required')
        found=c.execute('SELECT task_id,user_id FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone()
        if found is None:raise HTTPException(404,'Cleanup job not found')
        # Match deletion/scheduling lock order: user -> task -> cleanup job.
        if found['user_id']!=admin_id:tasks._user(c,found['user_id'])
        task=c.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(found['task_id'],)).fetchone()
        row=c.execute('SELECT * FROM cleanup_jobs WHERE id=%s FOR UPDATE',(job_id,)).fetchone();now=tasks.utcnow()
        if row['status']!='COMPLETED':
            row=c.execute("UPDATE cleanup_jobs SET status='PENDING',next_attempt_at=%s,manual_requested=true WHERE id=%s RETURNING *",(now,job_id)).fetchone()
        _audit(c,task,admin_id,'ADMIN_MANUAL_RETRY','REQUESTED',now)
        return _receipt(c,row)

def _delete_object(key):
    client=object_store.client();bucket=object_store.bucket()
    client.delete_object(Bucket=bucket,Key=key)
    try:client.head_object(Bucket=bucket,Key=key)
    except ClientError as error:
        if str(error.response['Error']['Code']) in ('404','NoSuchKey','NotFound','NoSuchBucket'):return
        raise
    raise CleanupFailure('OBJECT','OBJECT_STILL_PRESENT')

def _clean_asset(c,asset):
    key=asset['storage_key'];kind=asset['asset_kind']
    if kind=='PROCESSING_WORKSPACE':
        from app.processing import _workspace_path,_remove_workspace
        path,_=_workspace_path(key);_remove_workspace(path)
        if path.exists() or path.is_symlink():raise CleanupFailure(kind)
    elif kind in ('RESULT_JSON','RESULT_DOCUMENT'):
        match=re.fullmatch(r'db-result/([0-9a-f-]{36})/(export|presentation)',key)
        if not match:raise CleanupFailure(kind,'INVALID_ASSET_KEY')
        identifier=UUID(match.group(1))
        c.execute('DELETE FROM task_results WHERE id=%s AND task_id=%s AND user_id=%s',(identifier,asset['task_id'],asset['user_id']))
    else:
        # Only explicitly registered private object keys are eligible. Filesystem
        # paths or unrecognized storage namespaces are never treated as S3 data.
        if not key.startswith(('originals/','derived/','evaluation/','evaluations/','frames/','audio/','results/','exports/','cache/','temp/','annotations/','failure-cases/')) or '\x00' in key:
            raise CleanupFailure(kind,'UNKNOWN_ASSET')
        _delete_object(key)
    c.execute("UPDATE media_assets SET cleanup_state='DELETED',deleted_at=%s WHERE id=%s",(tasks.utcnow(),asset['id']))

def _purge_bodies_and_logs(c,task):
    tid=task['id']
    c.execute('DELETE FROM task_results WHERE task_id=%s',(tid,))
    for table in ('model_calls','schedule_events','retry_events','quota_events','task_consents','task_attempts'):
        c.execute('DELETE FROM '+table+' WHERE task_id=%s',(tid,))
    c.execute("DELETE FROM audit_events WHERE task_id=%s AND retention_class='TASK_30D'",(tid,))
    c.execute('DELETE FROM evaluation_links WHERE task_id=%s',(tid,))
    # Keep only a tombstone/receipt; filenames, errors, timings and content are
    # ordinary task data and must not survive as hidden processing history.
    c.execute('UPDATE tasks SET display_filename=NULL,duration_seconds=NULL,failure_category=NULL,failure_code=NULL,retry_count=0,cooldown_level=0,cooldown_until=NULL,started_at=NULL,finished_at=NULL,last_heartbeat_at=NULL WHERE id=%s',(tid,))

def process_job(job_id,manual=False):
    with tasks.connect() as c:
        found=c.execute('SELECT task_id,user_id FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone()
        if found is None:return None
        tasks._user(c,found['user_id'])
        task=c.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(found['task_id'],)).fetchone()
        job=c.execute('SELECT * FROM cleanup_jobs WHERE id=%s FOR UPDATE',(job_id,)).fetchone();now=tasks.utcnow()
        if job['status']=='COMPLETED':return _receipt(c,job)
        if job['status']=='MANUAL_REQUIRED' and not manual:return _receipt(c,job)
        if job['next_attempt_at'] and now<job['next_attempt_at'] and not manual and now-job['started_at']<=timedelta(minutes=30):return _receipt(c,job)
        manual=manual or job['manual_requested']
        elapsed=now-job['started_at']
        # Alert on elapsed wall time even when a long Worker lease makes it
        # unsafe to touch physical files. Manual retries never bypass safety.
        if elapsed>timedelta(minutes=30):
            _alert(c,job,'CLEANUP_TIME_LIMIT',now)
            if not manual:
                c.execute("UPDATE cleanup_jobs SET status='MANUAL_REQUIRED' WHERE id=%s",(job_id,))
                return _receipt(c,c.execute('SELECT * FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone())
        if job['worker_safe_after'] and now<job['worker_safe_after']:
            c.execute('UPDATE cleanup_jobs SET next_attempt_at=%s WHERE id=%s',(job['worker_safe_after'],job_id))
            return _receipt(c,c.execute('SELECT * FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone())
        c.execute("UPDATE cleanup_jobs SET status='RUNNING',attempt_count=attempt_count+1,last_attempt_at=%s,manual_requested=false WHERE id=%s",(now,job_id))
        failed=None
        for asset in c.execute("SELECT * FROM media_assets WHERE task_id=%s AND cleanup_state<>'DELETED' AND asset_kind<>'BACKUP' ORDER BY created_at,id",(task['id'],)).fetchall():
            try:
                with c.transaction():_clean_asset(c,asset)
            except Exception as error:
                kind=error.kind if isinstance(error,CleanupFailure) else asset['asset_kind']
                code=error.code if isinstance(error,CleanupFailure) else 'ASSET_CLEANUP_FAILED'
                c.execute("UPDATE media_assets SET cleanup_state='FAILED' WHERE id=%s",(asset['id'],));failed=(kind,code)
        if failed:
            attempts=job['attempt_count']+1; escalation=attempts>4 or elapsed>timedelta(minutes=30)
            wait=max(1,int(os.getenv('CLEANUP_RETRY_SECONDS','60')))
            c.execute("UPDATE cleanup_jobs SET status=%s,next_attempt_at=%s,last_error_kind=%s,last_error_code=%s WHERE id=%s",('MANUAL_REQUIRED' if escalation else 'PENDING',now+timedelta(seconds=wait),failed[0],failed[1],job_id))
            if escalation:_alert(c,job,'CLEANUP_RETRY_LIMIT' if attempts>4 else 'CLEANUP_TIME_LIMIT',now)
        else:
            _purge_bodies_and_logs(c,task)
            c.execute('UPDATE tasks SET deleted_at=COALESCE(deleted_at,%s) WHERE id=%s',(now,task['id']))
            c.execute("UPDATE cleanup_jobs SET status='COMPLETED',completed_at=%s,next_attempt_at=NULL,last_error_kind=NULL,last_error_code=NULL WHERE id=%s",(now,job_id))
            c.execute("UPDATE cleanup_alerts SET resolved_at=%s WHERE cleanup_job_id=%s AND resolved_at IS NULL AND alert_code IN ('CLEANUP_RETRY_LIMIT','CLEANUP_TIME_LIMIT')",(now,job_id))
            _audit(c,task,None,'ONLINE_CLEANUP','COMPLETED',now)
        return _receipt(c,c.execute('SELECT * FROM cleanup_jobs WHERE id=%s',(job_id,)).fetchone())

def run_once():
    now=tasks.utcnow()
    with tasks.connect() as c:
        expired=c.execute("SELECT id,user_id FROM tasks WHERE created_at<=%s AND deletion_requested_at IS NULL",(now-timedelta(days=30),)).fetchall()
    for row in expired:request_delete(row['id'],row['user_id'],reason='RETENTION_EXPIRED')
    with tasks.connect() as c:
        due=c.execute("SELECT id FROM cleanup_jobs WHERE status IN ('PENDING','RUNNING') AND (next_attempt_at IS NULL OR next_attempt_at<=%s OR started_at<%s) ORDER BY started_at,id",(now,now-timedelta(minutes=30))).fetchall()
    result=[process_job(row['id']) for row in due]
    from app.deletion_control import cleanup_backups
    with tasks.connect() as c:
        jobs=c.execute('SELECT * FROM cleanup_jobs').fetchall()
    for job in jobs:
        try:status=cleanup_backups(job['task_id'])
        except Exception:status='FAILED'
        with tasks.connect() as c:
            c.execute('UPDATE cleanup_jobs SET backup_cleanup_state=%s,backup_completed_at=%s WHERE id=%s',(status,now if status=='COMPLETED' else None,job['id']))
            if status=='FAILED' or status=='PENDING' and now-job['started_at']>=timedelta(days=30):_alert(c,job,'BACKUP_CLEANUP_UNVERIFIED',now)
    with tasks.connect() as c:
        c.execute("DELETE FROM audit_events WHERE retention_class='DELETION_90D' AND created_at<=%s",(now-timedelta(days=90),))
        c.execute("DELETE FROM audit_events a WHERE a.retention_class='TASK_30D' AND ((a.task_id IS NULL AND a.created_at<=%s) OR EXISTS (SELECT 1 FROM tasks t WHERE t.id=a.task_id AND t.created_at<=%s))",(now-timedelta(days=30),now-timedelta(days=30)))
        for expired_task in c.execute('SELECT * FROM tasks WHERE created_at<=%s AND deletion_requested_at IS NOT NULL',(now-timedelta(days=30),)).fetchall():
            _purge_bodies_and_logs(c,expired_task)
        # Deletion-control keys are retained only for the 90-day restoration
        # window. Unresolved jobs retain minimal controls and explicit alerts.
        completed=c.execute("SELECT task_id,id FROM cleanup_jobs WHERE status='COMPLETED' AND backup_cleanup_state IN ('COMPLETED','NOT_APPLICABLE') AND started_at<=%s",(now-timedelta(days=90),)).fetchall()
        for old in completed:
            c.execute('DELETE FROM evaluation_links WHERE task_id=%s',(old['task_id'],))
            c.execute('DELETE FROM media_assets WHERE task_id=%s',(old['task_id'],))
            c.execute('DELETE FROM cleanup_alerts WHERE cleanup_job_id=%s',(old['id'],))
            c.execute('DELETE FROM cleanup_jobs WHERE id=%s',(old['id'],))
            c.execute('DELETE FROM tasks WHERE id=%s',(old['task_id'],))
    return result
