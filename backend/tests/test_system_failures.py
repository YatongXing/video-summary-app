"""Cross-module failure injection against isolated PostgreSQL and offline evidence."""
import asyncio
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app import pipeline,processing,queue,tasks
from app.model_adapter import AdapterError
from app.worker import WorkerContext
from test_results import result_db,identity,claimed,candidate,client,authenticated
from test_processing import bridge


def test_commit_failure_rolls_back_bilingual_success_and_refunds_once(result_db,monkeypatch):
    db=result_db;uid,tid,generation=claimed(db)
    # A deferred trigger fails at COMMIT, after publication's SQL and task
    # success statements ran. A mocked publish exception cannot prove rollback.
    db.execute("""CREATE FUNCTION reject_result_commit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'synthetic deferred persistence failure'; END; $$""")
    db.execute("""CREATE CONSTRAINT TRIGGER reject_result_commit AFTER INSERT ON task_results
        DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN (NEW.language='en')
        EXECUTE FUNCTION reject_result_commit()""")
    response=SimpleNamespace(localized_candidates={'zh':candidate(),'en':candidate('en')},model_version='offline-fake-test')
    monkeypatch.setattr(pipeline.processing,'prepare_and_analyze',lambda *args,**kwargs:response)
    context=WorkerContext({'id':str(tid),'generation':generation},'result-test-worker')
    with pytest.raises(Exception,match='synthetic deferred persistence failure'):
        pipeline.run_pipeline(context)
    assert db.execute('SELECT status,failure_category FROM tasks WHERE id=%s',(tid,)).fetchone()==('FAILED','INTERNAL')
    assert db.execute('SELECT count(*) FROM task_results').fetchone()[0]==0
    assert db.execute("SELECT count(*) FROM media_assets WHERE asset_kind IN ('RESULT_JSON','RESULT_DOCUMENT')").fetchone()[0]==0
    assert db.execute('SELECT status FROM task_attempts WHERE task_id=%s',(tid,)).fetchone()[0]=='FAILED'
    tasks.fail_task(tid,generation,'INTERNAL',owner='result-test-worker')
    assert db.execute("SELECT count(*) FROM quota_events WHERE task_id=%s AND event_type='REFUND'",(tid,)).fetchone()[0]==1
    assert db.execute('SELECT credits_available FROM users WHERE id=%s',(uid,)).fetchone()[0]==3
    async def scenario():
        async with client() as c:
            authenticated(db,c,uid)
            assert (await c.get(f'/tasks/{tid}/export?language=zh')).status_code==409
            assert (await c.get(f'/tasks/{tid}/result?language=en')).status_code==409
    asyncio.run(scenario())


def test_private_storage_read_failure_fails_pipeline_without_model_or_export(bridge,monkeypatch):
    db,context,uid,root,calls=bridge
    class Unavailable:
        def get_object(self,**kwargs):raise TimeoutError('synthetic private storage outage')
    monkeypatch.setattr(processing.object_store,'client',lambda:Unavailable())
    with pytest.raises(AdapterError) as error:pipeline.run_pipeline(context)
    assert error.value.failure_category=='INTERNAL'
    assert db.execute('SELECT status,failure_category FROM tasks').fetchone()==('FAILED','INTERNAL')
    assert db.execute('SELECT count(*) FROM model_calls').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM task_results').fetchone()[0]==0
    assert db.execute('SELECT credits_available FROM users WHERE id=%s',(uid,)).fetchone()[0]==3
    assert db.execute("SELECT count(*) FROM quota_events WHERE event_type='REFUND'").fetchone()[0]==1
    assert not list(root.iterdir())


def test_killed_worker_process_recovers_once_and_old_lease_cannot_publish(result_db):
    db=result_db;uid=identity(db)
    tid=db.execute("INSERT INTO tasks(user_id,input_kind,display_filename,duration_seconds) VALUES (%s,'LOCAL','synthetic.mp4',2) RETURNING id",(uid,)).fetchone()[0]
    script="""import threading,time
from app.worker import run
def hold(context):
    time.sleep(120)
run(threading.Event(),interval_seconds=.01,processor=hold,worker_id='crash-fixture',heartbeat_seconds=.05)
"""
    child=subprocess.Popen([sys.executable,'-u','-c',script],env=os.environ.copy(),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+10
        while True:
            row=db.execute('SELECT status,lease_generation FROM tasks WHERE id=%s',(tid,)).fetchone()
            if row[0]=='PROCESSING':break
            if child.poll() is not None:pytest.fail('Synthetic Worker exited before claim: '+child.stderr.read().decode(errors='replace'))
            if time.monotonic()>deadline:pytest.fail('Synthetic Worker did not claim task')
            time.sleep(.02)
        old_generation=row[1]
        child.kill();child.wait(timeout=5)
        assert child.returncode!=0
        db.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 second' WHERE id=%s",(tid,))
        assert str(tid) in queue.recover_expired()
        queue.recover_expired()
        assert db.execute('SELECT status,failure_category FROM tasks WHERE id=%s',(tid,)).fetchone()==('FAILED','INTERNAL')
        assert db.execute("SELECT count(*) FROM quota_events WHERE task_id=%s AND event_type='REFUND'",(tid,)).fetchone()[0]==1
        with pytest.raises(HTTPException):queue.fenced_write(tid,old_generation,'crash-fixture',lambda c,t:c.execute("UPDATE tasks SET failure_code='BAD_OLD_WRITE' WHERE id=%s",(tid,)))
        tasks.request_retry(tid,uid,str(uuid4()))
        replacement=queue.claim_next('replacement-fixture')
        assert replacement['generation']>old_generation
        assert db.execute("SELECT count(*) FROM quota_events WHERE task_id=%s AND event_type='SPEND'",(tid,)).fetchone()[0]==1
        with pytest.raises(HTTPException):tasks.succeed_task(tid,old_generation,owner='crash-fixture')
        assert db.execute('SELECT status FROM tasks WHERE id=%s',(tid,)).fetchone()[0]=='PROCESSING'
    finally:
        if child.poll() is None:child.kill()
        child.wait(timeout=5)
        if child.stderr:child.stderr.close()
