"""Transactional task state and quota operations; no scheduling or model calls."""
from app import maintenance
from datetime import datetime, timedelta, timezone
import math
import os
import psycopg
from psycopg.rows import dict_row
import re
from uuid import UUID
from fastapi import HTTPException

def connect():
    """Bound task/Worker database waits without replacing DSN search_path options."""
    timeout=int(os.getenv('TASK_DB_TIMEOUT_SECONDS','5'))
    if timeout<=0: raise ValueError('Task database timeout must be positive')
    connection=psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=timeout)
    try:
        connection.execute("SELECT set_config('statement_timeout',%s,false),set_config('lock_timeout',%s,false)",(str(timeout*1000),str(timeout*1000)))
    except BaseException:
        connection.close()
        raise
    return connection

SIX_HOURS = timedelta(hours=6)
REFUNDABLE = {'INTERNAL','EXTERNAL_TIMEOUT','EXTERNAL_RATE_LIMIT','EXTERNAL_UNAVAILABLE','UNKNOWN'}
CATEGORIES = REFUNDABLE | {'USER_INPUT','USER_CANCELLED'}

def utcnow():
    return datetime.now(timezone.utc)

def _user(c, user_id):
    row=c.execute('SELECT * FROM users WHERE id=%s FOR NO KEY UPDATE',(user_id,)).fetchone()
    if row is None: raise HTTPException(404,'账户不存在。')
    return row

def _task(c,task_id,user_id):
    row=c.execute('SELECT * FROM tasks WHERE id=%s AND user_id=%s FOR UPDATE',(task_id,user_id)).fetchone()
    from app.deletion_control import blocked
    if row is None or row['deletion_requested_at'] or row['deleted_at'] or blocked(task_id):
        raise HTTPException(404,'任务不存在。')
    return row

def _locks(c,task_id,user_id=None):
    if user_id is None:
        found=c.execute('SELECT user_id FROM tasks WHERE id=%s',(task_id,)).fetchone()
        if found is None: raise HTTPException(404,'任务不存在。')
        user_id=found['user_id']
    return _user(c,user_id),_task(c,task_id,user_id)

def _save_user(c,u,now):
    c.execute('UPDATE users SET credits_available=%s,recovery_started_at=%s,updated_at=%s WHERE id=%s',
              (u['credits_available'],u['recovery_started_at'],now,u['id']))

def _recover(c,u,now):
    if u['role']=='ADMIN': return
    if u['credits_available']>=3:
        u['recovery_started_at']=None
    elif u['recovery_started_at'] is None:
        u['recovery_started_at']=now
    else:
        anchor=u['recovery_started_at']
        due=max(0,int((now-anchor)//SIX_HOURS))
        for step in range(1,min(due,3-u['credits_available'])+1):
            boundary=anchor+SIX_HOURS*step
            inserted=c.execute("INSERT INTO quota_events(user_id,event_key,event_type,delta,created_at) VALUES (%s,%s,'RECOVER',1,%s) ON CONFLICT DO NOTHING RETURNING id",(u['id'],f"recover:{u['id']}:{boundary.isoformat()}",boundary)).fetchone()
            if inserted: u['credits_available']+=1
        if u['credits_available']==3: u['recovery_started_at']=None
        elif due: u['recovery_started_at']=anchor+SIX_HOURS*due
    _save_user(c,u,now)

def _quota(u,now):
    unlimited=u['role']=='ADMIN'
    seconds=None if unlimited or u['recovery_started_at'] is None else max(0,math.ceil((u['recovery_started_at']+SIX_HOURS-now).total_seconds()))
    return {'credits':None if unlimited else u['credits_available'],'unlimited':unlimited,'recovery_seconds':seconds}

def quota(user_id):
    maintenance.ensure_open()
    with connect() as c:
        u=_user(c,user_id);now=utcnow();_recover(c,u,now)
        maintenance.ensure_open()
        return _quota(u,now)

def _audit(c,t,reason,result,now):
    c.execute("INSERT INTO audit_events(target_user_id,task_id,event_type,reason_code,result,retention_class,created_at) VALUES (%s,%s,'TASK_STATE',%s,%s,'TASK_30D',%s)",(t['user_id'],t['id'],reason,result,now))

def _metadata(c,t,u,now):
    cooldown=max(0,math.ceil((t['cooldown_until']-now).total_seconds())) if t['cooldown_until'] else 0
    reason='TERMINAL' if t['status'] in ('SUCCEEDED','CANCELLED') else 'NOT_FAILED' if t['status']!='FAILED' else 'RETRY_LIMIT' if t['retry_count']>=20 else 'COOLDOWN' if cooldown else None
    events={r['event_type'] for r in c.execute('SELECT event_type FROM quota_events WHERE task_id=%s',(t['id'],)).fetchall()}
    latest_retry=c.execute('SELECT max(accepted_at) AS at FROM retry_events WHERE task_id=%s',(t['id'],)).fetchone()['at']
    queued_at=max(t['submitted_at'],latest_retry) if latest_retry else t['submitted_at']
    refund='UNLIMITED' if u['role']=='ADMIN' else 'REFUNDED' if 'REFUND' in events else 'CHARGED' if 'SPEND' in events else 'NOT_CHARGED'
    return {'id':str(t['id']),'status':t['status'],'filename':t['display_filename'],
            'created_at':t['created_at'],'submitted_at':t['submitted_at'],'started_at':t['started_at'],'finished_at':t['finished_at'],
            'retry_count':t['retry_count'],'cooldown_seconds':cooldown,'retry_allowed':reason is None,'retry_reason':reason,
            'failure_category':t['failure_category'],'failure_code':t['failure_code'],'refund_status':refund,
            'api_fee_warning':t['timeout_retry_count']>=4,'generation':t['lease_generation'],
            'lease_owner':t['lease_owner'],'lease_expires_at':t['lease_expires_at'],
            'duration_seconds':float(t['duration_seconds']) if t['duration_seconds'] is not None else None,
            'queue_seconds':max(0,((t['started_at'] or (t['finished_at'] if t['status'] in ('FAILED','CANCELLED') else now))-queued_at).total_seconds()),
            'processing_seconds':max(0,((t['finished_at'] or now)-t['started_at']).total_seconds()) if t['started_at'] else 0,
            'progress_percent':100 if t['status']=='SUCCEEDED' else 0 if t['status']=='QUEUED' else None,
            'target_seconds':1200 if t['duration_seconds'] is not None and float(t['duration_seconds'])==3600 else None}

def get_task(task_id,user_id):
    maintenance.ensure_open()
    with connect() as c:
        u,t=_locks(c,task_id,user_id)
        maintenance.ensure_open()
        return _metadata(c,t,u,utcnow())

def list_tasks(user_id,limit=50,offset=0):
    maintenance.ensure_open()
    if not 1<=limit<=100 or offset<0: raise HTTPException(422,'分页参数无效。')
    with connect() as c:
        u=_user(c,user_id);now=utcnow()
        rows=c.execute('SELECT * FROM tasks WHERE user_id=%s AND deletion_requested_at IS NULL AND deleted_at IS NULL ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s',(user_id,limit,offset)).fetchall()
        from app.deletion_control import blocked
        maintenance.ensure_open()
        return [_metadata(c,t,u,now) for t in rows if not blocked(t['id'])]

def cancel_task(task_id,user_id):
    maintenance.ensure_open()
    with connect() as c:
        u,t=_locks(c,task_id,user_id);now=utcnow()
        if t['status'] in ('SUCCEEDED','CANCELLED'):
            result=_metadata(c,t,u,now)
            maintenance.ensure_open()
            return result
        if t['status']=='FAILED': raise HTTPException(409,'失败任务可重试，当前不能取消。')
        c.execute("UPDATE tasks SET status='CANCELLED',failure_category='USER_CANCELLED',failure_code='USER_CANCELLED',finished_at=%s,lease_owner=NULL,lease_expires_at=NULL,next_retry_at=NULL WHERE id=%s",(now,task_id))
        c.execute("UPDATE task_attempts SET status='CANCELLED',failure_category='USER_CANCELLED',failure_code='USER_CANCELLED',finished_at=%s WHERE task_id=%s AND status='PROCESSING'",(now,task_id))
        _audit(c,t,'USER_CANCELLED','CANCELLED',now)
        result=_metadata(c,_task(c,task_id,user_id),u,now)
        maintenance.ensure_open()
        return result

def request_retry(task_id,user_id,request_key,source='USER'):
    maintenance.ensure_open()
    if source not in ('USER','SYSTEM'): raise ValueError('Invalid retry source')
    try: request_key=str(UUID(str(request_key)))
    except (ValueError,TypeError,AttributeError): raise HTTPException(422,'重试请求标识无效。') from None
    with connect() as c:
        u,t=_locks(c,task_id,user_id);now=utcnow()
        if c.execute('SELECT id FROM retry_events WHERE task_id=%s AND request_key=%s',(task_id,request_key)).fetchone():
            result=_metadata(c,t,u,now)
            maintenance.ensure_open()
            return result
        if t['retry_count']>=20: raise HTTPException(409,'已达到累计 20 次重试上限。')
        if t['cooldown_until'] and now<t['cooldown_until']: raise HTTPException(429,'任务正在冷却，请稍后重试。')
        if t['status']!='FAILED': raise HTTPException(409,'当前任务状态不允许重试。')
        # Rolling window is (now - 5 minutes, now]; exactly five minutes old expires.
        count=c.execute('SELECT count(*) AS n FROM retry_events WHERE task_id=%s AND accepted_at>%s AND accepted_at<=%s',(task_id,now-timedelta(minutes=5),now)).fetchone()['n']
        triggered=count>=4
        level=t['cooldown_level']+int(triggered)
        until=now+timedelta(minutes=(1,5,15,30)[min(level-1,3)]) if triggered else None
        timeout_count=t['timeout_retry_count']+int(t['failure_category']=='EXTERNAL_TIMEOUT')
        c.execute('INSERT INTO retry_events(task_id,user_id,request_key,source,accepted_at,cooldown_triggered) VALUES (%s,%s,%s,%s,%s,%s)',(task_id,user_id,request_key,source,now,triggered))
        c.execute("UPDATE tasks SET status='QUEUED',retry_count=retry_count+1,cooldown_level=%s,cooldown_until=%s,next_retry_at=%s,timeout_retry_count=%s,failure_category=NULL,failure_code=NULL,started_at=NULL,finished_at=NULL WHERE id=%s",(level,until,until,timeout_count,task_id))
        _audit(c,t,'RETRY_'+source,'QUEUED',now)
        result=_metadata(c,_task(c,task_id,user_id),u,now)
        maintenance.ensure_open()
        return result

def start_task(task_id,worker_id='worker'):
    maintenance.ensure_open()
    from app.queue import start_task as start
    return start(task_id,worker_id)


def _start_locked(c,u,t,now,worker_id):
    maintenance.ensure_open()
    from app.queue import settings
    task_id=t['id']
    if u['disabled_at'] is not None: raise HTTPException(409,'账户已停用，不能开始任务。')
    if t['status']=='PROCESSING':
        if t['lease_owner']!=worker_id: raise HTTPException(409,'任务已由其他 Worker 开始。')
        from app.queue import assert_fence
        assert_fence(t,u,t['lease_generation'],worker_id,now)
        result=_metadata(c,t,u,now)
        maintenance.ensure_open()
        return result
    if t['status']!='QUEUED': raise HTTPException(409,'当前任务不能开始。')
    if t['cooldown_until'] and now<t['cooldown_until']: raise HTTPException(429,'任务正在冷却。')
    if u['role']=='USER' and c.execute("SELECT id FROM tasks WHERE user_id=%s AND status='PROCESSING' AND id<>%s",(u['id'],task_id)).fetchone(): raise HTTPException(409,'用户已有活动任务。')
    _recover(c,u,now)
    spent=c.execute("SELECT id FROM quota_events WHERE task_id=%s AND event_type='SPEND'",(task_id,)).fetchone()
    if u['role']!='ADMIN' and not spent:
        if u['credits_available']<=0: raise HTTPException(409,'处理机会不足。')
        c.execute("INSERT INTO quota_events(user_id,task_id,event_key,event_type,delta,created_at) VALUES (%s,%s,%s,'SPEND',-1,%s)",(u['id'],task_id,f'spend:{task_id}',now))
        u['credits_available']-=1
        if u['recovery_started_at'] is None: u['recovery_started_at']=now
        _save_user(c,u,now)
    if c.execute("SELECT count(*) AS n FROM tasks WHERE status='PROCESSING'").fetchone()['n'] >= settings()['concurrency']:
        raise HTTPException(409,'系统处理名额已满。')
    generation=t['lease_generation']+1
    number=c.execute('SELECT count(*) AS n FROM task_attempts WHERE task_id=%s',(task_id,)).fetchone()['n']
    c.execute("INSERT INTO task_attempts(task_id,user_id,attempt_number,lease_generation,status,started_at) VALUES (%s,%s,%s,%s,'PROCESSING',%s)",(task_id,u['id'],number,generation,now))
    c.execute("UPDATE tasks SET status='PROCESSING',lease_generation=%s,lease_owner=%s,started_at=%s,finished_at=NULL,lease_expires_at=%s,last_heartbeat_at=%s WHERE id=%s",(generation,worker_id,now,now+timedelta(seconds=settings()['lease']),now,task_id))
    _audit(c,t,'START','PROCESSING',now)
    result=_metadata(c,_task(c,task_id,u['id']),u,now)
    maintenance.ensure_open()
    return result

def _finish(task_id,generation,category=None,failure_code=None,owner=None):
    maintenance.ensure_open()
    from app.queue import assert_fence
    with connect() as c:
        u,t=_locks(c,task_id);now=utcnow()
        if t['lease_generation']!=generation: raise HTTPException(409,'处理尝试已失效。')
        if t['status'] in ('FAILED','CANCELLED','SUCCEEDED'): return _metadata(c,t,u,now)
        assert_fence(t,u,generation,owner,now)
        maintenance.ensure_open()
        result=_finish_locked(c,u,t,now,category,failure_code)
        maintenance.ensure_open()
        return result


def _finish_locked(c,u,t,now,category=None,failure_code=None):
    maintenance.ensure_open()
    task_id=t['id']
    generation=t['lease_generation']
    if category is not None and (not isinstance(category,str) or category not in CATEGORIES): category='UNKNOWN'
    status='CANCELLED' if category=='USER_CANCELLED' else 'FAILED' if category is not None else 'SUCCEEDED'
    code=failure_code if isinstance(failure_code,str) and re.fullmatch('[A-Z][A-Z0-9_]{0,63}',failure_code) else category
    if category in REFUNDABLE and u['role']!='ADMIN':
        _recover(c,u,now)
        spent=c.execute("SELECT id FROM quota_events WHERE task_id=%s AND event_type='SPEND'",(task_id,)).fetchone()
        if spent:
            inserted=c.execute("INSERT INTO quota_events(user_id,task_id,event_key,event_type,delta,created_at) VALUES (%s,%s,%s,'REFUND',1,%s) ON CONFLICT DO NOTHING RETURNING id",(u['id'],task_id,f'refund:{task_id}',now)).fetchone()
            if inserted:
                u['credits_available']=min(3,u['credits_available']+1)
                if u['credits_available']==3:u['recovery_started_at']=None
                _save_user(c,u,now)
    c.execute('UPDATE tasks SET status=%s,failure_category=%s,failure_code=%s,finished_at=%s,lease_owner=NULL,lease_expires_at=NULL WHERE id=%s',(status,category,code,now,task_id))
    c.execute("UPDATE task_attempts SET status=%s,failure_category=%s,failure_code=%s,finished_at=%s WHERE task_id=%s AND lease_generation=%s AND status='PROCESSING'",(status,category,code,now,task_id,generation))
    _audit(c,t,category or 'OUTPUT_VALIDATED',status,now)
    result=_metadata(c,_task(c,task_id,u['id']),u,now)
    maintenance.ensure_open()
    return result

def fail_task(task_id,generation,category='UNKNOWN',failure_code=None,owner=None):
    if category is None: category='UNKNOWN'
    return _finish(task_id,generation,category,failure_code,owner)

def succeed_task(task_id,generation,owner=None):
    """Trusted internal caller must validate required outputs before invoking."""
    return _finish(task_id,generation,owner=owner)
