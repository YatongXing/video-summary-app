"""Independent S3-06 scheduling, lease and real PostgreSQL race checks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from uuid import UUID, uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest
from fastapi import HTTPException

@pytest.fixture
def db(monkeypatch):
    base=os.getenv('TEST_DATABASE_URL')
    if not base: pytest.skip('Explicit isolated TEST_DATABASE_URL required')
    if not conninfo_to_dict(base).get('dbname','').endswith('_test'): pytest.fail('Only *_test databases allowed')
    schema='queue_test_'+uuid4().hex
    with psycopg.connect(base,autocommit=True) as control:
        control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        monkeypatch.setenv('DATABASE_URL',make_conninfo(base,options='-csearch_path='+schema))
        monkeypatch.setenv('QUEUE_GLOBAL_CONCURRENCY','4')
        monkeypatch.setenv('WORKER_LEASE_SECONDS','120')
        monkeypatch.setenv('WORKER_HEARTBEAT_SECONDS','30')
        from app.migrate import run_migrations
        try:
            with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as c:
                run_migrations(c)
                yield c
        finally: control.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

@pytest.fixture
def clock(monkeypatch):
    from app import tasks, queue
    value=[datetime.now(timezone.utc)+timedelta(seconds=2)]
    monkeypatch.setattr(tasks,'utcnow',lambda:value[0])
    if hasattr(queue,'utcnow'): monkeypatch.setattr(queue,'utcnow',lambda:value[0])
    return value

def user(c,role='USER',identifier=None):
    return c.execute('INSERT INTO users(id,email_normalized,password_hash,role) VALUES (%s,%s,%s,%s) RETURNING id',(identifier or uuid4(),str(uuid4())+'@example.invalid','synthetic-hash',role)).fetchone()[0]

def task(c,uid,at=None,identifier=None):
    return c.execute("INSERT INTO tasks(id,user_id,input_kind,submitted_at) VALUES (%s,%s,'LOCAL',COALESCE(%s,now())) RETURNING id",(identifier or uuid4(),uid,at)).fetchone()[0]

def events(c,kind): return c.execute('SELECT count(*) FROM quota_events WHERE event_type=%s',(kind,)).fetchone()[0]

def test_fifo_user_and_task_ties_and_admin_no_priority(db,clock):
    from app import queue, tasks
    a=user(db,identifier=UUID(int=1));b=user(db,'ADMIN',UUID(int=2))
    late=task(db,b,clock[0],UUID(int=3)); second=task(db,a,clock[0],UUID(int=2));first=task(db,a,clock[0],UUID(int=1))
    r=queue.claim_next('one');assert r['id']==str(first)
    tasks.succeed_task(first,r['generation'])
    r=queue.claim_next('two');assert r['id']==str(second)
    tasks.succeed_task(second,r['generation'])
    assert queue.claim_next('three')['id']==str(late)


def test_only_active_user_can_be_bypassed_and_oldest_runs_when_free(db,clock):
    from app import queue,tasks
    a=user(db); b=user(db); active=task(db,a,clock[0]-timedelta(seconds=2))
    running=tasks.start_task(active,'active')
    first=task(db,a,clock[0]);second=task(db,a,clock[0]+timedelta(seconds=1));other=task(db,b,clock[0]+timedelta(seconds=2))
    r=queue.claim_next('other');assert r['id']==str(other)
    tasks.succeed_task(active,running['generation'])
    r=queue.claim_next('oldest');assert r['id']==str(first)
    assert db.execute('SELECT status FROM tasks WHERE id=%s',(second,)).fetchone()[0]=='QUEUED'


@pytest.mark.parametrize('reason',['cooldown','no_credits','disabled'])
def test_nonconcurrency_head_blocker_never_skipped(db,clock,reason):
    from app import queue
    a=user(db);b=user(db);head=task(db,a,clock[0]);tail=task(db,b,clock[0]+timedelta(seconds=1))
    if reason=='cooldown':db.execute('UPDATE tasks SET cooldown_until=%s WHERE id=%s',(clock[0]+timedelta(minutes=1),head))
    if reason=='no_credits':db.execute('UPDATE users SET credits_available=0,recovery_started_at=%s WHERE id=%s',(clock[0],a))
    if reason=='disabled':db.execute('UPDATE users SET disabled_at=now() WHERE id=%s',(a,))
    assert queue.claim_next('blocked') is None
    assert db.execute('SELECT status FROM tasks WHERE id=%s',(tail,)).fetchone()[0]=='QUEUED'


def test_multiple_blockers_do_not_qualify_for_bypass(db,clock):
    from app import queue,tasks
    a=user(db);b=user(db);active=task(db,a,clock[0]-timedelta(seconds=1));tasks.start_task(active,'active')
    head=task(db,a,clock[0]);task(db,b,clock[0]+timedelta(seconds=1))
    db.execute('UPDATE tasks SET cooldown_until=%s WHERE id=%s',(clock[0]+timedelta(minutes=1),head))
    assert queue.claim_next('blocked') is None


def test_concurrent_claims_enforce_global_cap_and_spend_once(db,clock,monkeypatch):
    from app import queue
    monkeypatch.setenv('QUEUE_GLOBAL_CONCURRENCY','2')
    for _ in range(6):task(db,user(db))
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(lambda n:queue.claim_next('w'+str(n)),range(6)))
    claimed=[r for r in results if r]
    assert len(claimed)==2 and len({r['id'] for r in claimed})==2
    assert events(db,'SPEND')==2
    assert db.execute("SELECT count(*) FROM tasks WHERE status='PROCESSING'").fetchone()[0]==2


def test_direct_start_cannot_bypass_global_capacity(db,clock,monkeypatch):
    from app import queue,tasks
    monkeypatch.setenv('QUEUE_GLOBAL_CONCURRENCY','1')
    first=task(db,user(db));second=task(db,user(db));queue.claim_next('one')
    with pytest.raises(HTTPException):tasks.start_task(second,'direct')
    assert events(db,'SPEND')==1


def test_lease_expiry_refunds_once_and_old_worker_is_fenced(db,clock):
    from app import queue,tasks
    uid=user(db);tid=task(db,uid);r=queue.claim_next('old');gen=r['generation']
    assert queue.heartbeat(tid,gen,'wrong') is False
    clock[0]+=timedelta(seconds=121)
    assert queue.heartbeat(tid,gen,'old') is False
    with pytest.raises(HTTPException):tasks.succeed_task(tid,gen)
    with pytest.raises(HTTPException):queue.fenced_write(tid,gen,'old',lambda c,t:c.execute("UPDATE tasks SET failure_code='LATE' WHERE id=%s",(tid,)))
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:queue.recover_expired(),range(2)))
    assert db.execute('SELECT status,failure_category FROM tasks WHERE id=%s',(tid,)).fetchone()==('FAILED','INTERNAL')
    assert events(db,'REFUND')==1
    assert db.execute('SELECT credits_available FROM users WHERE id=%s',(uid,)).fetchone()[0]==3
    tasks.request_retry(tid,uid,uuid4())
    new=queue.claim_next('new');assert new['generation']>gen
    assert events(db,'SPEND')==1
    with pytest.raises(HTTPException):tasks.fail_task(tid,gen,'INTERNAL')


def test_heartbeat_renews_and_cancel_or_delete_rejects_writes(db,clock):
    from app import queue,tasks
    uid=user(db);tid=task(db,uid);r=queue.claim_next('worker');gen=r['generation']
    original=db.execute('SELECT lease_expires_at FROM tasks WHERE id=%s',(tid,)).fetchone()[0]
    clock[0]+=timedelta(seconds=30)
    assert queue.heartbeat(tid,gen,'worker') is True
    assert db.execute('SELECT lease_expires_at FROM tasks WHERE id=%s',(tid,)).fetchone()[0]>original
    queue.fenced_write(tid,gen,'worker',lambda c,t:c.execute("UPDATE tasks SET failure_code='CHECKED' WHERE id=%s",(tid,)))
    tasks.cancel_task(tid,uid)
    assert queue.heartbeat(tid,gen,'worker') is False
    with pytest.raises(HTTPException):queue.fenced_write(tid,gen,'worker',lambda c,t:None)
    second=task(db,uid);r=queue.claim_next('second')
    db.execute('UPDATE tasks SET deletion_requested_at=now() WHERE id=%s',(second,))
    assert queue.heartbeat(second,r['generation'],'second') is False
    with pytest.raises(HTTPException):queue.fenced_write(second,r['generation'],'second',lambda c,t:None)


def test_schedule_audit_records_bypass_and_sort_keys(db,clock):
    from app import queue,tasks
    a=user(db);b=user(db);active=task(db,a,clock[0]-timedelta(seconds=1));tasks.start_task(active,'active')
    blocked=task(db,a,clock[0]);chosen=task(db,b,clock[0]+timedelta(seconds=1));queue.claim_next('next')
    columns={r[0] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='schedule_events'")}
    assert 'created_at' in columns and ('task_id' in columns or 'selected_task_id' in columns)
    rows=db.execute('SELECT row_to_json(s)::text FROM schedule_events s').fetchall()
    evidence=' '.join(r[0] for r in rows)
    assert str(blocked) in evidence and str(chosen) in evidence
    assert str(a) in evidence and str(b) in evidence


@pytest.mark.parametrize('key,value',[('QUEUE_GLOBAL_CONCURRENCY','0'),('WORKER_LEASE_SECONDS','0'),('WORKER_HEARTBEAT_SECONDS','120')])
def test_invalid_scheduler_configuration_rejected(db,monkeypatch,key,value):
    from app import queue
    monkeypatch.setenv(key,value)
    with pytest.raises(ValueError):queue.claim_next('invalid-config')


def test_owner_and_exact_expiry_fence(db,clock):
    from app import queue,tasks
    tid=task(db,user(db));r=queue.claim_next('owner')
    with pytest.raises(HTTPException):tasks.succeed_task(tid,r['generation'],owner='intruder')
    with pytest.raises(HTTPException):queue.fenced_write(tid,r['generation'],'intruder',lambda c,t:None)
    clock[0]+=timedelta(seconds=120)
    assert queue.heartbeat(tid,r['generation'],'owner') is False
    with pytest.raises(HTTPException):tasks.succeed_task(tid,r['generation'],owner='owner')


def test_disabled_account_fences_existing_worker(db,clock):
    from app import queue,tasks
    uid=user(db);tid=task(db,uid);r=queue.claim_next('worker')
    db.execute('UPDATE users SET disabled_at=now() WHERE id=%s',(uid,))
    assert not queue.heartbeat(tid,r['generation'],'worker')
    with pytest.raises(HTTPException):queue.fenced_write(tid,r['generation'],'worker',lambda c,t:None)


def test_claim_cancel_race_never_resurrects_cancelled_task(db,clock):
    from app import queue,tasks
    uid=user(db);tid=task(db,uid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claim=pool.submit(queue.claim_next,'racing-worker');cancel=pool.submit(tasks.cancel_task,tid,uid)
        claimed=claim.result();cancel.result()
    assert db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='CANCELLED'
    assert events(db,'SPEND') in (0,1) and events(db,'REFUND')==0
    if claimed:
        with pytest.raises(HTTPException):queue.fenced_write(tid,claimed['generation'],'racing-worker',lambda c,t:None)
