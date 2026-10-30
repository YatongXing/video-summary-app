"""Independent synthetic processor lifecycle checks; never call a model."""
import threading
import time
from test_queue import db, user, task


def launch(stop,processor,**kwargs):
    from app.worker import run
    errors=[]
    def invoke():
        try:run(stop,interval_seconds=.01,processor=processor,worker_id='test-worker',heartbeat_seconds=.02,**kwargs)
        except BaseException as error:errors.append(error)
    thread=threading.Thread(target=invoke,daemon=True);thread.start()
    return thread,errors


def wait_for(predicate,timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.02)
    raise AssertionError('Worker did not reach expected state')


def test_no_processor_never_claims_queued_task(db):
    tid=task(db,user(db));stop=threading.Event();thread,errors=launch(stop,None)
    time.sleep(.1);stop.set();thread.join(3)
    assert not thread.is_alive() and not errors
    assert db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='QUEUED'
    assert db.execute('SELECT count(*) FROM quota_events').fetchone()[0]==0


def test_synthetic_validated_processor_completes_once(db):
    tid=task(db,user(db));stop=threading.Event();seen=[]
    def processor(ctx):
        seen.append(ctx.id)
        assert ctx.checkpoint()
        ctx.fenced_write(lambda c,t:c.execute("UPDATE tasks SET failure_code='SYNTHETIC' WHERE id=%s",(tid,)))
        ctx.complete();stop.set()
    thread,errors=launch(stop,processor);thread.join(5);stop.set()
    assert not thread.is_alive() and not errors and len(seen)==1
    assert db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='SUCCEEDED'
    assert db.execute("SELECT count(*) FROM quota_events WHERE event_type='SPEND'").fetchone()[0]==1


def test_processor_exception_refunds_and_does_not_auto_requeue(db,caplog):
    tid=task(db,user(db));stop=threading.Event()
    def processor(ctx):raise RuntimeError('sensitive-body-must-not-be-logged')
    thread,errors=launch(stop,processor)
    try:wait_for(lambda:db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='FAILED')
    finally:stop.set();thread.join(4)
    assert not thread.is_alive() and not errors
    assert db.execute('SELECT failure_category FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='INTERNAL'
    assert db.execute("SELECT count(*) FROM quota_events WHERE event_type='REFUND'").fetchone()[0]==1
    assert db.execute('SELECT count(*) FROM retry_events').fetchone()[0]==0
    assert 'sensitive-body-must-not-be-logged' not in caplog.text


def test_user_cancel_reaches_running_context_and_blocks_late_write(db):
    from app import tasks
    uid=user(db);tid=task(db,uid);stop=threading.Event();entered=threading.Event();exited=threading.Event();late=[]
    def processor(ctx):
        entered.set()
        if ctx.cancelled.wait(4):
            try:ctx.fenced_write(lambda c,t:c.execute("UPDATE tasks SET failure_code='BAD_LATE' WHERE id=%s",(tid,)))
            except Exception:late.append('rejected')
        exited.set()
    thread,errors=launch(stop,processor)
    try:
        assert entered.wait(4)
        tasks.cancel_task(tid,uid)
        assert exited.wait(4)
    finally:stop.set();thread.join(4)
    assert not thread.is_alive() and not errors and late==['rejected']
    assert db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='CANCELLED'
    assert db.execute("SELECT count(*) FROM quota_events WHERE event_type='REFUND'").fetchone()[0]==0



def test_uncooperative_processor_cannot_block_shutdown_or_write_late(db,monkeypatch):
    monkeypatch.setenv('WORKER_SHUTDOWN_SECONDS','.2')
    tid=task(db,user(db));stop=threading.Event();entered=threading.Event();release=threading.Event();done=threading.Event();late=[]
    def processor(ctx):
        entered.set();release.wait(5)
        try:ctx.complete()
        except Exception:late.append('rejected')
        finally:done.set()
    thread,errors=launch(stop,processor)
    try:
        assert entered.wait(4)
        before=time.monotonic();stop.set();thread.join(1.5)
        assert not thread.is_alive() and time.monotonic()-before<1.5 and not errors
        release.set();assert done.wait(3)
        assert late==['rejected']
        assert db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]!='SUCCEEDED'
    finally:release.set();stop.set();thread.join(3);done.wait(3)
