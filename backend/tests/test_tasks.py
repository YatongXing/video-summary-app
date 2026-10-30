"""S3-05 independent transaction, fake-clock and HTTP acceptance tests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4
import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest
from fastapi import HTTPException

@pytest.fixture
def db(monkeypatch):
    url=os.getenv('TEST_DATABASE_URL')
    if not url: pytest.skip('Explicit isolated TEST_DATABASE_URL required')
    if not conninfo_to_dict(url).get('dbname','').endswith('_test'): pytest.fail('Only *_test databases allowed')
    schema='tasks_test_'+uuid4().hex
    with psycopg.connect(url,autocommit=True) as control:
        control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        scoped=make_conninfo(url,options='-csearch_path='+schema)
        monkeypatch.setenv('DATABASE_URL',scoped)
        # Quota/state tests advance hours; lease expiry is covered separately in S3-06.
        monkeypatch.setenv('WORKER_LEASE_SECONDS','86400')
        monkeypatch.setenv('WORKER_HEARTBEAT_SECONDS','30')
        monkeypatch.setenv('QUEUE_GLOBAL_CONCURRENCY','16')
        from app.migrate import run_migrations
        try:
            with psycopg.connect(scoped,autocommit=True) as connection:
                run_migrations(connection)
                yield connection
        finally: control.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

@pytest.fixture
def clock(monkeypatch):
    from app import tasks
    value=[datetime.now(timezone.utc)+timedelta(seconds=2)]
    monkeypatch.setattr(tasks,'utcnow',lambda:value[0])
    return value

def user(db,role='USER'):
    return db.execute('INSERT INTO users(email_normalized,password_hash,role) VALUES (%s,%s,%s) RETURNING id',(str(uuid4())+'@example.invalid','synthetic-hash',role)).fetchone()[0]

def task(db,uid):
    return db.execute("INSERT INTO tasks(user_id,input_kind) VALUES (%s,'LOCAL') RETURNING id",(uid,)).fetchone()[0]

def balance(db,uid): return db.execute('SELECT credits_available FROM users WHERE id=%s',(uid,)).fetchone()[0]
def state(db,tid): return db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]
def count(db,event): return db.execute('SELECT count(*) FROM quota_events WHERE event_type=%s',(event,)).fetchone()[0]
def generation(result): return result['generation']

def test_recovery_anchor_not_reset_by_later_use_and_stops_at_cap(db,clock):
    from app import tasks
    uid=user(db); assert tasks.quota(uid)['credits']==3
    t0=clock[0]; first=task(db,uid); gen=generation(tasks.start_task(first)); tasks.succeed_task(first,gen); assert balance(db,uid)==2
    clock[0]=t0+timedelta(hours=1); second=task(db,uid); gen=generation(tasks.start_task(second)); tasks.succeed_task(second,gen); assert balance(db,uid)==1
    clock[0]=t0+timedelta(hours=6); assert tasks.quota(uid)['credits']==2
    clock[0]=t0+timedelta(hours=12); assert tasks.quota(uid)['credits']==3
    assert db.execute('SELECT recovery_started_at FROM users WHERE id=%s',(uid,)).fetchone()[0] is None
    clock[0]=t0+timedelta(hours=13); tasks.start_task(task(db,uid))
    assert db.execute('SELECT recovery_started_at FROM users WHERE id=%s',(uid,)).fetchone()[0]==clock[0]
    clock[0]=t0+timedelta(days=3); assert tasks.quota(uid)['credits']==3

def test_refund_independent_of_timer_and_duplicate_failure(db,clock):
    from app import tasks
    uid=user(db); ids=[task(db,uid) for _ in range(3)]; t0=clock[0]
    gens=[]
    for index,tid in enumerate(ids):
        clock[0]=t0+timedelta(hours=index); gens.append(generation(tasks.start_task(tid)))
        if index<2: tasks.succeed_task(tid,gens[-1])
    assert balance(db,uid)==0
    clock[0]=t0+timedelta(hours=3)
    tasks.fail_task(ids[2],gens[2],'INTERNAL'); tasks.fail_task(ids[2],gens[2],'INTERNAL')
    assert balance(db,uid)==1 and count(db,'REFUND')==1
    assert db.execute('SELECT recovery_started_at FROM users WHERE id=%s',(uid,)).fetchone()[0]==t0
    clock[0]=t0+timedelta(hours=6); assert tasks.quota(uid)['credits']==2

@pytest.mark.parametrize('category,refund',[('INTERNAL',True),('EXTERNAL_TIMEOUT',True),('EXTERNAL_RATE_LIMIT',True),('EXTERNAL_UNAVAILABLE',True),('UNKNOWN',True),('USER_INPUT',False),('USER_CANCELLED',False)])
def test_failure_categories_and_no_retry_recharge(db,clock,category,refund):
    from app import tasks
    uid=user(db); tid=task(db,uid); gen=generation(tasks.start_task(tid))
    tasks.fail_task(tid,gen,category)
    assert balance(db,uid)==(3 if refund else 2)
    assert count(db,'REFUND')==int(refund)
    if category!='USER_CANCELLED':
        tasks.request_retry(tid,uid,str(uuid4()))
        gen=generation(tasks.start_task(tid)); tasks.fail_task(tid,gen,category)
        assert count(db,'SPEND')==1 and count(db,'REFUND')==int(refund)
        assert balance(db,uid)==(3 if refund else 2)

def test_queue_cancel_processing_cancel_and_success_finality(db,clock):
    from app import tasks
    uid=user(db); queued=task(db,uid)
    tasks.cancel_task(queued,uid); tasks.cancel_task(queued,uid)
    assert state(db,queued)=='CANCELLED' and balance(db,uid)==3 and count(db,'SPEND')==0
    with pytest.raises(HTTPException): tasks.request_retry(queued,uid,str(uuid4()))
    processing=task(db,uid); gen=generation(tasks.start_task(processing)); tasks.cancel_task(processing,uid)
    tasks.fail_task(processing,gen,'INTERNAL')
    assert state(db,processing)=='CANCELLED' and balance(db,uid)==2 and count(db,'REFUND')==0
    succeeded=task(db,uid); gen=generation(tasks.start_task(succeeded)); tasks.succeed_task(succeeded,gen); tasks.cancel_task(succeeded,uid)
    assert state(db,succeeded)=='SUCCEEDED'

def test_two_workers_and_repeated_failure_only_spend_refund_once(db,clock):
    from app import tasks
    uid=user(db); tid=task(db,uid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        started=list(pool.map(lambda _:tasks.start_task(tid),range(2)))
    assert len({generation(r) for r in started})==1
    assert count(db,'SPEND')==1 and balance(db,uid)==2
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:tasks.fail_task(tid,generation(started[0]),'INTERNAL'),range(2)))
    assert count(db,'REFUND')==1 and balance(db,uid)==3

def test_admin_unlimited_has_no_credit_events(db,clock):
    from app import tasks
    uid=user(db,'ADMIN'); assert tasks.quota(uid)['unlimited'] is True and tasks.quota(uid)['credits'] is None
    for _ in range(5):
        tid=task(db,uid); gen=generation(tasks.start_task(tid)); tasks.fail_task(tid,gen,'INTERNAL')
    assert count(db,'SPEND')==0 and count(db,'REFUND')==0

def test_retry_fifth_triggers_cooldown_rejections_do_not_extend_or_count(db,clock):
    from app import tasks
    uid=user(db); tid=task(db,uid); gen=generation(tasks.start_task(tid)); tasks.fail_task(tid,gen,'INTERNAL')
    keys=[]
    for index in range(5):
        key=str(uuid4());keys.append(key); tasks.request_retry(tid,uid,key,source='SYSTEM' if index%2 else 'USER')
        if index<4:
            gen=generation(tasks.start_task(tid));tasks.fail_task(tid,gen,'INTERNAL');clock[0]+=timedelta(seconds=1)
    before=db.execute('SELECT retry_count,cooldown_until,cooldown_level FROM tasks WHERE id=%s',(tid,)).fetchone()
    assert before[0]==5 and before[2]==1 and before[1]==clock[0]+timedelta(minutes=1)
    tasks.request_retry(tid,uid,keys[-1])
    with pytest.raises(HTTPException): tasks.request_retry(tid,uid,str(uuid4()))
    with pytest.raises(HTTPException): tasks.start_task(tid)
    assert db.execute('SELECT retry_count,cooldown_until,cooldown_level FROM tasks WHERE id=%s',(tid,)).fetchone()==before
    clock[0]=before[1];tasks.start_task(tid)

@pytest.mark.parametrize('level,minutes',[(0,1),(1,5),(2,15),(3,30),(4,30)])
def test_cooldown_staircase(db,clock,level,minutes):
    from app import tasks
    uid=user(db);tid=task(db,uid)
    db.execute("UPDATE tasks SET status='FAILED',retry_count=4,cooldown_level=%s WHERE id=%s",(level,tid))
    for offset in range(4): db.execute("INSERT INTO retry_events(task_id,user_id,request_key,source,accepted_at) VALUES (%s,%s,%s,'USER',%s)",(tid,uid,str(uuid4()),clock[0]-timedelta(seconds=offset+1)))
    tasks.request_retry(tid,uid,str(uuid4()))
    until=db.execute('SELECT cooldown_until FROM tasks WHERE id=%s',(tid,)).fetchone()[0]
    assert until==clock[0]+timedelta(minutes=minutes)

def test_retry_twenty_limit_and_old_request_key_replay(db,clock):
    from app import tasks
    uid=user(db);tid=task(db,uid);gen=generation(tasks.start_task(tid));tasks.fail_task(tid,gen,'INTERNAL');first=None
    for index in range(20):
        key=str(uuid4()); first=first or key
        tasks.request_retry(tid,uid,key,source='USER' if index%2 else 'SYSTEM')
        gen=generation(tasks.start_task(tid));tasks.fail_task(tid,gen,'INTERNAL');clock[0]+=timedelta(minutes=6)
    tasks.request_retry(tid,uid,first)
    with pytest.raises(HTTPException): tasks.request_retry(tid,uid,str(uuid4()))
    assert db.execute('SELECT retry_count FROM tasks WHERE id=%s',(tid,)).fetchone()[0]==20
    assert count(db,'SPEND')==1 and count(db,'REFUND')==1

def test_exact_rolling_window_lower_boundary_excluded(db,clock):
    from app import tasks
    uid=user(db);tid=task(db,uid)
    db.execute("UPDATE tasks SET status='FAILED',retry_count=4 WHERE id=%s",(tid,))
    for seconds in (300,299,200,100): db.execute("INSERT INTO retry_events(task_id,user_id,request_key,source,accepted_at) VALUES (%s,%s,%s,'USER',%s)",(tid,uid,str(uuid4()),clock[0]-timedelta(seconds=seconds)))
    tasks.request_retry(tid,uid,str(uuid4()))
    assert db.execute('SELECT cooldown_until FROM tasks WHERE id=%s',(tid,)).fetchone()[0] is None

def test_stale_generation_and_deleted_task_fences(db,clock):
    from app import tasks
    uid=user(db);tid=task(db,uid);gen=generation(tasks.start_task(tid))
    for fn in (tasks.fail_task,tasks.succeed_task):
        with pytest.raises(HTTPException): fn(tid,gen+1)
    assert state(db,tid)=='PROCESSING'
    db.execute('UPDATE tasks SET deletion_requested_at=%s WHERE id=%s',(clock[0],tid))
    for action in (lambda:tasks.get_task(tid,uid),lambda:tasks.cancel_task(tid,uid),lambda:tasks.request_retry(tid,uid,str(uuid4())),lambda:tasks.start_task(tid)):
        with pytest.raises(HTTPException): action()

def test_http_private_list_quota_cancel_retry_csrf(db):
    async def scenario():
        from app.main import app
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000',headers={'Origin':'http://127.0.0.1:8000'}) as c:
            assert (await c.get('/quota')).status_code==401
            data={'email':'taskowner@example.invalid','password':'Synthetic-test-only-42!'}
            registered=await c.post('/auth/register',json=data); assert registered.status_code==201
            uid=registered.json()['id']; tid=task(db,uid);other=task(db,user(db))
            login=await c.post('/auth/login',json=data);csrf=login.json()['csrf_token']
            quota=await c.get('/quota');assert quota.status_code==200 and quota.json()['credits']==3
            listed=await c.get('/tasks');assert listed.status_code==200 and {r['id'] for r in listed.json()}=={str(tid)}
            assert (await c.get('/tasks/'+str(other))).status_code==404
            assert (await c.post('/tasks/'+str(tid)+'/cancel')).status_code==403
            assert (await c.post('/tasks/'+str(other)+'/cancel',headers={'X-CSRF-Token':csrf})).status_code==404
            assert (await c.post('/tasks/'+str(tid)+'/cancel',headers={'X-CSRF-Token':csrf})).status_code==200
            assert (await c.post('/tasks/'+str(tid)+'/retry',json={'request_key':str(uuid4())},headers={'X-CSRF-Token':csrf})).status_code==409
            assert (await c.post('/tasks/'+str(tid)+'/start',headers={'X-CSRF-Token':csrf})).status_code in (404,405)
    asyncio.run(scenario())


@pytest.mark.parametrize('category',['','UNCLASSIFIED_ERROR'])
def test_unknown_categories_never_become_success(db,clock,category):
    from app import tasks
    uid=user(db);tid=task(db,uid);gen=generation(tasks.start_task(tid))
    tasks.fail_task(tid,gen,category)
    assert state(db,tid)=='FAILED' and balance(db,uid)==3
    assert db.execute('SELECT failure_category FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='UNKNOWN'


def test_refund_after_natural_recovery_never_exceeds_three(db,clock):
    from app import tasks
    uid=user(db);tid=task(db,uid);gen=generation(tasks.start_task(tid))
    clock[0]+=timedelta(hours=6);assert tasks.quota(uid)['credits']==3
    tasks.fail_task(tid,gen,'INTERNAL');tasks.fail_task(tid,gen,'INTERNAL')
    assert balance(db,uid)==3 and count(db,'REFUND')==1


def test_distinct_workers_and_distinct_tasks_respect_single_active_user(db,clock):
    from app import tasks
    uid=user(db);tid=task(db,uid)
    def begin(worker):
        try:return tasks.start_task(tid,worker_id=worker)
        except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(begin,['worker-a','worker-b']))
    assert sum(isinstance(r,dict) for r in results)==1 and 409 in results
    assert count(db,'SPEND')==1
    other=task(db,uid)
    with pytest.raises(HTTPException):tasks.start_task(other,'worker-c')
    assert state(db,other)=='QUEUED' and count(db,'SPEND')==1


def test_cancel_failure_race_has_one_final_category_and_settlement(db,clock):
    from app import tasks
    uid=user(db);tid=task(db,uid);gen=generation(tasks.start_task(tid))
    def action(kind):
        try:return tasks.cancel_task(tid,uid) if kind=='cancel' else tasks.fail_task(tid,gen,'INTERNAL')
        except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(action,['cancel','fail']))
    final=state(db,tid);assert final in ('CANCELLED','FAILED')
    assert count(db,'REFUND')==int(final=='FAILED')
    assert balance(db,uid)==(3 if final=='FAILED' else 2)


def test_disabled_user_cannot_start_and_failure_before_start_cannot_refund(db,clock):
    from app import tasks
    uid=user(db);tid=task(db,uid)
    with pytest.raises(HTTPException):tasks.fail_task(tid,0,'INTERNAL')
    assert count(db,'REFUND')==0 and balance(db,uid)==3
    db.execute('UPDATE users SET disabled_at=%s WHERE id=%s',(clock[0],uid))
    with pytest.raises(HTTPException):tasks.start_task(tid)
    assert count(db,'SPEND')==0


def test_fourth_timeout_retry_warns_without_extra_spend(db,clock):
    from app import tasks
    uid=user(db);tid=task(db,uid);gen=generation(tasks.start_task(tid));tasks.fail_task(tid,gen,'EXTERNAL_TIMEOUT')
    for index in range(4):
        metadata=tasks.request_retry(tid,uid,str(uuid4()))
        if index<3:
            gen=generation(tasks.start_task(tid));tasks.fail_task(tid,gen,'EXTERNAL_TIMEOUT')
        else:assert metadata['api_fee_warning'] is True
    assert count(db,'SPEND')==1 and count(db,'REFUND')==1

def test_worker_reported_user_cancellation_is_terminal(db,clock):
    from app import tasks
    uid=user(db);tid=task(db,uid);gen=generation(tasks.start_task(tid))
    tasks.fail_task(tid,gen,'USER_CANCELLED')
    assert state(db,tid)=='CANCELLED' and balance(db,uid)==2 and count(db,'REFUND')==0
    with pytest.raises(HTTPException):tasks.request_retry(tid,uid,str(uuid4()))
    assert db.execute('SELECT status FROM task_attempts WHERE task_id=%s',(tid,)).fetchone()[0]=='CANCELLED'
