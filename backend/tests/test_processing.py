"""Independent private-media bridge and PostgreSQL fencing checks."""
import io
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest
from app import processing, tasks, queue
from app.worker import WorkerContext
from app.model_adapter import FakeModelAdapter, AdapterError

@pytest.fixture
def bridge(monkeypatch,tmp_path):
    base=os.getenv('TEST_DATABASE_URL')
    if not base:pytest.skip('Explicit *_test PostgreSQL database required')
    if not conninfo_to_dict(base).get('dbname','').endswith('_test'):pytest.fail('Only *_test database allowed')
    if not shutil.which('ffmpeg'):pytest.skip('Run Docker test target with FFmpeg')
    schema='processing_test_'+uuid4().hex
    with psycopg.connect(base,autocommit=True) as control:
        control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        scoped=make_conninfo(base,options='-csearch_path='+schema)
        monkeypatch.setenv('DATABASE_URL',scoped)
        monkeypatch.setenv('PROCESSING_WORK_DIR',str(tmp_path/'processing'))
        monkeypatch.setenv('WORKER_LEASE_SECONDS','3600')
        from app.migrate import run_migrations
        try:
            with psycopg.connect(scoped,autocommit=True) as db:
                run_migrations(db)
                uid=db.execute("INSERT INTO users(email_normalized,password_hash) VALUES ('private@example.invalid','synthetic-hash') RETURNING id").fetchone()[0]
                tid=db.execute("INSERT INTO tasks(user_id,input_kind,display_filename) VALUES (%s,'LOCAL','private-name.mp4') RETURNING id",(uid,)).fetchone()[0]
                db.execute("INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key) VALUES (%s,%s,'ORIGINAL','private-object-key')",(tid,uid))
                clip=tmp_path/'source.mp4'
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=32x32:r=5:d=1','-f','lavfi','-i','sine=frequency=400:duration=1','-c:v','libx264','-threads','1','-c:a','aac','-shortest',str(clip)],capture_output=True,check=True,timeout=30)
                raw=clip.read_bytes();calls=[]
                class Store:
                    def get_object(self,**kwargs):
                        calls.append(kwargs);return {'Body':io.BytesIO(raw),'ContentLength':len(raw)}
                monkeypatch.setattr(processing.object_store,'client',lambda:Store())
                context=WorkerContext(queue.claim_next('bridge-test'),'bridge-test')
                yield db,context,uid,tmp_path/'processing',calls
        finally:control.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))

def test_candidate_is_not_task_success_and_metadata_is_private(bridge):
    db,context,uid,root,calls=bridge
    result=processing.prepare_and_analyze(context,FakeModelAdapter())
    assert {o.modality for o in result.observations}=={'audio','visual'}
    assert db.execute('SELECT status FROM tasks').fetchone()[0]=='PROCESSING'
    row=db.execute('SELECT status,request_id,input_tokens,estimated_cost,model_version,adapter_version,prompt_version,policy_version FROM model_calls').fetchone()
    assert row[0]=='SUCCEEDED' and row[1].startswith('fake-') and row[2] is None and row[3] is None
    assert all(row[4:])
    assert 'private-name' not in str(row) and str(root) not in str(row)
    assert not list(root.iterdir())
    assert db.execute("SELECT cleanup_state FROM media_assets WHERE asset_kind='PROCESSING_WORKSPACE'").fetchone()[0]=='DELETED'
    assert len(calls)==1

@pytest.mark.parametrize('kind,category,credits',[('timeout','EXTERNAL_TIMEOUT',3),('rate_limit','EXTERNAL_RATE_LIMIT',3),('unavailable','EXTERNAL_UNAVAILABLE',3),('input','USER_INPUT',2),('internal','INTERNAL',3)])
def test_adapter_failure_is_recorded_once_and_classified(bridge,kind,category,credits):
    db,context,uid,root,calls=bridge
    with pytest.raises(AdapterError):processing.prepare_and_analyze(context,FakeModelAdapter(kind))
    assert db.execute('SELECT status,failure_category FROM tasks').fetchone()==('FAILED',category)
    assert db.execute('SELECT credits_available FROM users').fetchone()[0]==credits
    row=db.execute('SELECT status,error_category,estimated_cost,input_tokens FROM model_calls').fetchone()
    assert row==('FAILED',category,None,None)
    assert not list(root.iterdir()) and len(calls)==1

def test_duplicate_generation_does_not_make_second_model_call(bridge):
    db,context,uid,root,calls=bridge
    class Counting(FakeModelAdapter):
        count=0
        def analyze(self,*args,**kwargs):self.count+=1;return super().analyze(*args,**kwargs)
    adapter=Counting();processing.prepare_and_analyze(context,adapter)
    with pytest.raises(AdapterError):processing.prepare_and_analyze(context,adapter)
    assert adapter.count==1 and db.execute('SELECT count(*) FROM model_calls').fetchone()[0]==1
    assert db.execute('SELECT status FROM tasks').fetchone()[0]=='PROCESSING'

def test_cancelled_or_old_context_cannot_create_records(bridge):
    db,context,uid,root,calls=bridge
    tasks.cancel_task(context.id,uid)
    with pytest.raises(processing.ProcessingCancelled):processing.prepare_and_analyze(context,FakeModelAdapter())
    assert db.execute('SELECT count(*) FROM model_calls').fetchone()[0]==0
    assert not calls

def test_cancel_during_adapter_prevents_late_result_registration(bridge):
    db,context,uid,root,calls=bridge
    class Cancelling(FakeModelAdapter):
        def analyze(self,request,checkpoint=None):
            result=super().analyze(request)
            tasks.cancel_task(context.id,uid)
            return result
    with pytest.raises(processing.ProcessingCancelled):processing.prepare_and_analyze(context,Cancelling())
    assert db.execute('SELECT status FROM tasks').fetchone()[0]=='CANCELLED'
    assert db.execute("SELECT count(*) FROM model_calls WHERE status='SUCCEEDED'").fetchone()[0]==0
    assert not list(root.iterdir())

def test_failed_workspace_cleanup_is_registered_and_recoverable(bridge,monkeypatch):
    db,context,uid,root,calls=bridge
    original=processing._remove_workspace
    def failed(path):raise OSError('synthetic cleanup failure')
    monkeypatch.setattr(processing,'_remove_workspace',failed)
    with pytest.raises(AdapterError):processing.prepare_and_analyze(context,FakeModelAdapter())
    assert db.execute("SELECT cleanup_state FROM media_assets WHERE asset_kind='PROCESSING_WORKSPACE'").fetchone()[0]=='FAILED'
    monkeypatch.setattr(processing,'_remove_workspace',original)
    assert processing.recover_workspaces()
    assert not list(root.iterdir())


def test_late_response_preserves_known_usage_but_is_not_success(bridge,monkeypatch):
    import time
    from decimal import Decimal
    from dataclasses import replace
    from app.model_adapter import TokenUsage
    db,context,uid,root,calls=bridge
    monkeypatch.setenv('MODEL_ADAPTER_TIMEOUT_SECONDS','.01')
    class Late(FakeModelAdapter):
        def analyze(self,request,checkpoint=None):
            result=replace(super().analyze(request),usage=TokenUsage(input_tokens=123,output_tokens=4,estimated_cost=Decimal('.03'),currency='CNY'))
            time.sleep(.03)
            return result
    with pytest.raises(AdapterError) as error:processing.prepare_and_analyze(context,Late())
    assert error.value.kind=='timeout'
    row=db.execute('SELECT status,input_tokens,output_tokens,estimated_cost,currency,request_id FROM model_calls').fetchone()
    assert row[:5]==('FAILED',123,4,Decimal('.03'),'CNY') and row[5].startswith('fake-')
    assert db.execute('SELECT status FROM tasks').fetchone()[0]=='FAILED'

def test_accepted_original_corruption_is_internal_failure_with_refund(bridge,monkeypatch):
    db,context,uid,root,calls=bridge
    def corrupt(key,path,checkpoint):path.write_text('<html>corrupt storage object</html>')
    monkeypatch.setattr(processing,'download_original',corrupt)
    with pytest.raises(AdapterError):processing.prepare_and_analyze(context,FakeModelAdapter())
    assert db.execute('SELECT status,failure_category FROM tasks').fetchone()==('FAILED','INTERNAL')
    assert db.execute('SELECT credits_available FROM users').fetchone()[0]==3
    assert not list(root.iterdir())

def test_live_workspace_is_preserved_and_cancelled_workspace_recovered_by_worker(bridge):
    import threading,time
    from app.worker import run
    db,context,uid,root,calls=bridge
    with processing._workspace(context) as directory:
        (directory/'derived.wav').write_bytes(b'synthetic derived media')
        assert processing.recover_workspaces()==[] and directory.exists()
        tasks.cancel_task(context.id,uid)
        stop=threading.Event();thread=threading.Thread(target=run,args=(stop,.01),daemon=True)
        thread.start()
        try:
            for _ in range(40):
                if not directory.exists():break
                time.sleep(.05)
            assert not directory.exists()
            assert db.execute("SELECT cleanup_state FROM media_assets WHERE asset_kind='PROCESSING_WORKSPACE'").fetchone()[0]=='DELETED'
        finally:stop.set();thread.join(timeout=2)
        assert not thread.is_alive()


def test_invalid_server_timeout_refunds_internal_failure(bridge,monkeypatch):
    db,context,uid,root,calls=bridge
    monkeypatch.setenv('MODEL_ADAPTER_TIMEOUT_SECONDS','0')
    with pytest.raises(AdapterError) as error:processing.prepare_and_analyze(context,FakeModelAdapter())
    assert error.value.failure_category=='INTERNAL'
    assert db.execute('SELECT failure_category FROM tasks').fetchone()[0]=='INTERNAL'
    assert db.execute('SELECT credits_available FROM users').fetchone()[0]==3

def test_call_records_actual_returned_model_and_adapter_versions(bridge):
    from dataclasses import replace
    db,context,uid,root,calls=bridge
    class Revision(FakeModelAdapter):
        def analyze(self,*args,**kwargs):return replace(super().analyze(*args,**kwargs),model_version='actual-model-r2',adapter_version='actual-adapter-r2')
    processing.prepare_and_analyze(context,Revision())
    assert db.execute('SELECT model_version,adapter_version FROM model_calls').fetchone()==('actual-model-r2','actual-adapter-r2')
