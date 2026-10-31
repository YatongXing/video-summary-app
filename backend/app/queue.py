"""PostgreSQL queue authority: FIFO with only the approved user-active exception."""
from datetime import timedelta
import os
from fastapi import HTTPException
from app import tasks

LOCK_ID=773506001

def settings():
    values={'concurrency':int(os.getenv('QUEUE_GLOBAL_CONCURRENCY','1')),
            'lease':int(os.getenv('WORKER_LEASE_SECONDS','120')),
            'heartbeat':int(os.getenv('WORKER_HEARTBEAT_SECONDS','30'))}
    if min(values.values())<=0 or values['heartbeat']>=values['lease']:
        raise ValueError('Queue limits must be positive and heartbeat shorter than lease')
    return values

def _owner(worker_id):
    if not isinstance(worker_id,str) or not worker_id or len(worker_id)>128:
        raise ValueError('Invalid worker identifier')

def assert_fence(task,user,generation,worker_id,now):
    from app.deletion_control import blocked
    if (task['status']!='PROCESSING' or task['lease_generation']!=generation
        or worker_id is not None and task['lease_owner']!=worker_id
        or task['lease_expires_at'] is None or task['lease_expires_at']<=now
        or task['deletion_requested_at'] or task['deleted_at'] or user['disabled_at'] or blocked(task['id'])):
        raise HTTPException(409,'任务租约已失效，禁止写入。')

def _event(c,t,worker_id,kind,reason,now,related=None):
    if kind=='BLOCK':
        prior=c.execute('SELECT task_id,reason_code,event_type FROM schedule_events ORDER BY id DESC LIMIT 1').fetchone()
        if prior and prior['task_id']==t['id'] and prior['reason_code']==reason and prior['event_type']=='BLOCK':
            return
    c.execute('INSERT INTO schedule_events(task_id,user_id,submitted_at,worker_id,generation,event_type,reason_code,related_task_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
              (t['id'],t['user_id'],t['submitted_at'],worker_id,t['lease_generation'],kind,reason,related,now))

def _recover_locked(c,now):
    rows=c.execute("SELECT id,user_id FROM tasks WHERE status='PROCESSING' AND (lease_expires_at IS NULL OR lease_expires_at<=%s) ORDER BY user_id,id",(now,)).fetchall()
    recovered=[]
    for row in rows:
        u=tasks._user(c,row['user_id'])
        t=c.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(row['id'],)).fetchone()
        if t['status']!='PROCESSING' or t['lease_expires_at'] and t['lease_expires_at']>now:
            continue
        deleted=bool(t['deletion_requested_at'] or t['deleted_at'])
        if deleted:
            # Deletion already forbids content; release the active slot without refunding a user cancel.
            c.execute("UPDATE tasks SET status='CANCELLED',failure_category='USER_CANCELLED',failure_code='DELETION_REQUESTED',finished_at=%s,lease_owner=NULL,lease_expires_at=NULL WHERE id=%s",(now,t['id']))
            c.execute("UPDATE task_attempts SET status='CANCELLED',failure_category='USER_CANCELLED',failure_code='DELETION_REQUESTED',finished_at=%s WHERE task_id=%s AND status='PROCESSING'",(now,t['id']))
            tasks._audit(c,t,'DELETION_REQUESTED','CANCELLED',now)
        else:
            tasks._finish_locked(c,u,t,now,'INTERNAL','WORKER_LOST')
        c.execute('UPDATE tasks SET lease_generation=lease_generation+1 WHERE id=%s',(t['id'],))
        _event(c,t,t['lease_owner'] or 'recovery','RECOVER','WORKER_LOST',now)
        recovered.append(str(t['id']))
    return recovered

def recover_expired():
    settings()
    with tasks.connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_ID,))
        return _recover_locked(c,tasks.utcnow())

def _block_reason(c,u,t,now):
    if u['disabled_at']: return 'ACCOUNT_DISABLED'
    if t['cooldown_until'] and t['cooldown_until']>now: return 'COOLDOWN'
    if t['next_retry_at'] and t['next_retry_at']>now: return 'RETRY_WAIT'
    tasks._recover(c,u,now)
    spent=c.execute("SELECT id FROM quota_events WHERE task_id=%s AND event_type='SPEND'",(t['id'],)).fetchone()
    if u['role']!='ADMIN' and not spent and u['credits_available']<=0: return 'NO_CREDITS'
    return None

def claim_next(worker_id):
    _owner(worker_id);config=settings()
    with tasks.connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_ID,))
        now=tasks.utcnow();_recover_locked(c,now)
        if c.execute("SELECT count(*) AS n FROM tasks WHERE status='PROCESSING'").fetchone()['n']>=config['concurrency']:
            return None
        rows=c.execute("SELECT id,user_id FROM tasks WHERE status='QUEUED' AND deletion_requested_at IS NULL AND deleted_at IS NULL ORDER BY submitted_at,user_id,id").fetchall()
        bypassed=[];blocked_users=set()
        for row in rows:
            from app.deletion_control import blocked
            if blocked(row['id']): continue
            if row['user_id'] in blocked_users: continue
            u=tasks._user(c,row['user_id'])
            t=c.execute('SELECT * FROM tasks WHERE id=%s FOR UPDATE',(row['id'],)).fetchone()
            if t['status']!='QUEUED' or t['deletion_requested_at'] or t['deleted_at']: continue
            reason=_block_reason(c,u,t,now)
            if reason:
                _event(c,t,worker_id,'BLOCK',reason,now)
                return None
            active=c.execute("SELECT id FROM tasks WHERE user_id=%s AND status='PROCESSING'",(u['id'],)).fetchone()
            if u['role']=='USER' and active:
                blocked_users.add(u['id']);bypassed.append(t)
                continue
            result=tasks._start_locked(c,u,t,now,worker_id)
            started=c.execute('SELECT * FROM tasks WHERE id=%s',(t['id'],)).fetchone()
            _event(c,started,worker_id,'CLAIM','FIFO' if not bypassed else 'USER_ACTIVE_BYPASS',now)
            for earlier in bypassed:
                _event(c,earlier,worker_id,'BYPASS','USER_ACTIVE',now,t['id'])
            return result
        if bypassed: _event(c,bypassed[0],worker_id,'BLOCK','USER_ACTIVE',now)
        return None

def start_task(task_id,worker_id):
    """Internal transaction primitive; real workers select tasks with claim_next."""
    _owner(worker_id);settings()
    with tasks.connect() as c:
        c.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_ID,))
        now=tasks.utcnow();_recover_locked(c,now)
        u,t=tasks._locks(c,task_id)
        if t['status']=='QUEUED':
            reason=_block_reason(c,u,t,now)
            if reason: raise HTTPException(409,'任务尚不符合开始条件。')
        return tasks._start_locked(c,u,t,now,worker_id)

def heartbeat(task_id,generation,worker_id):
    _owner(worker_id);config=settings()
    with tasks.connect() as c:
        try:
            u,t=tasks._locks(c,task_id);now=tasks.utcnow()
            assert_fence(t,u,generation,worker_id,now)
        except HTTPException: return False
        c.execute('UPDATE tasks SET lease_expires_at=%s,last_heartbeat_at=%s WHERE id=%s',
                  (now+timedelta(seconds=config['lease']),now,task_id))
        return True

def fenced_write(task_id,generation,worker_id,callback):
    """Only database effects: external objects must be staged before this registration transaction."""
    _owner(worker_id)
    with tasks.connect() as c:
        u,t=tasks._locks(c,task_id)
        assert_fence(t,u,generation,worker_id,tasks.utcnow())
        result=callback(c,t)
        assert_fence(t,u,generation,worker_id,tasks.utcnow())
        return result
