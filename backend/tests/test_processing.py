"""Independent private-media bridge and PostgreSQL fencing checks."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Thread
from unittest.mock import patch
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
        monkeypatch.setenv('DELETION_JOURNAL_DIR',str(tmp_path/'journal'))
        from app.migrate import run_migrations
        try:
            with psycopg.connect(scoped,autocommit=True) as db:
                run_migrations(db)
                import json
                from app.deletion_control import initialize
                initialize()
                runtime=Path(os.environ['DELETION_JOURNAL_DIR'])/'runtime';runtime.mkdir(exist_ok=True)
                (runtime/'producer-ready').write_text(json.dumps({'deployment_id':str(db.execute('SELECT deployment_id FROM deletion_control_state').fetchone()[0])}))
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

@pytest.mark.parametrize('kind,category,credits',[('timeout','EXTERNAL_TIMEOUT',1),('rate_limit','EXTERNAL_RATE_LIMIT',1),('unavailable','EXTERNAL_UNAVAILABLE',1),('input','USER_INPUT',0),('internal','INTERNAL',1)])
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
    assert db.execute('SELECT credits_available FROM users').fetchone()[0]==1
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
            deadline=time.monotonic()+2
            while True:
                state=db.execute("SELECT cleanup_state FROM media_assets WHERE asset_kind='PROCESSING_WORKSPACE'").fetchone()[0]
                if not directory.exists() and state=='DELETED':break
                if time.monotonic()>=deadline:break
                time.sleep(.05)
            assert not directory.exists()
            assert state=='DELETED'
        finally:stop.set();thread.join(timeout=2)
        assert not thread.is_alive()


def test_invalid_server_timeout_refunds_internal_failure(bridge,monkeypatch):
    db,context,uid,root,calls=bridge
    monkeypatch.setenv('MODEL_ADAPTER_TIMEOUT_SECONDS','0')
    with pytest.raises(AdapterError) as error:processing.prepare_and_analyze(context,FakeModelAdapter())
    assert error.value.failure_category=='INTERNAL'
    assert db.execute('SELECT failure_category FROM tasks').fetchone()[0]=='INTERNAL'
    assert db.execute('SELECT credits_available FROM users').fetchone()[0]==1

def test_call_records_actual_returned_model_and_adapter_versions(bridge):
    from dataclasses import replace
    db,context,uid,root,calls=bridge
    class Revision(FakeModelAdapter):
        def analyze(self,*args,**kwargs):return replace(super().analyze(*args,**kwargs),model_version='actual-model-r2',adapter_version='actual-adapter-r2')
    processing.prepare_and_analyze(context,Revision())
    assert db.execute('SELECT model_version,adapter_version FROM model_calls').fetchone()==('actual-model-r2','actual-adapter-r2')

@pytest.mark.parametrize('lost_phase', [None, 'ASR_SUBMITTING'])
def test_unconfirmed_request_cleans_workspace_and_preserves_only_asr_uncertainty(bridge, monkeypatch, lost_phase):
    from app import deepseek_adapter

    db, context, uid, root, calls = bridge
    db.execute("UPDATE tasks SET requested_model='deepseek-v4.1-flash' WHERE id=%s", (context.id,))
    adapter = deepseek_adapter.DeepSeekModelAdapter('sk-ws-synthetic-test')
    model_calls = []

    def upload(path, checkpoint):
        checkpoint()
        with path.open('rb') as audio:
            assert audio.read(8)[4:] == b'ftyp'
        if lost_phase is None:
            raise AdapterError('timeout')
        return 'oss://dashscope-instant/fixture/audio.m4a'

    def lost_response(*args):
        model_calls.append(args[1:3])
        raise AdapterError('timeout')

    monkeypatch.setattr(adapter, 'upload_audio', upload)
    monkeypatch.setattr(deepseek_adapter, '_json_request', lost_response)
    with pytest.raises(AdapterError) as failure:
        processing.prepare_and_analyze(context, adapter)
    assert failure.value.kind == 'timeout'
    assert model_calls == ([('POST', deepseek_adapter.ASR_PATH)] if lost_phase else [])
    assert db.execute("SELECT status,deepseek_phase FROM model_calls").fetchone() == ('FAILED', lost_phase)
    assert db.execute("SELECT status,failure_category FROM tasks").fetchone() == ('FAILED', 'EXTERNAL_TIMEOUT')
    assert db.execute("SELECT count(*) FROM task_results").fetchone() == (0,)
    assert not list(root.iterdir())
    assert db.execute("SELECT count(*) FROM media_assets WHERE asset_kind='ASR_AUDIO_TRANSFER'").fetchone() == (0,)


def test_legacy_asr_asset_recovers_with_switch_off(bridge, monkeypatch):
    from app import qwen_cloud

    db, context, uid, root, calls = bridge
    upload_key = f'vsa-qwen/asr/{context.id}/{uuid4().hex}.wav'
    objects = {upload_key, 'another-owner-object'}
    delete_calls = []

    class Cloud:
        bucket = 'synthetic-asr'
        backend = 'S3:' + 'b' * 64

        def remove(self, asset, checkpoint=None):
            checkpoint()
            assert asset['storage_key'] == upload_key
            delete_calls.append(upload_key)
            if len(delete_calls) == 1:
                raise OSError('synthetic temporary removal failure')
            objects.remove(upload_key)

    store = Cloud()
    tasks.cancel_task(context.id, uid)
    db.execute('''INSERT INTO media_assets(task_id,user_id,asset_kind,storage_key,storage_backend,storage_bucket)
                  VALUES (%s,%s,'ASR_AUDIO_TRANSFER',%s,%s,%s)''',
               (context.id, uid, upload_key, store.backend, store.bucket))
    monkeypatch.setenv('DEEPSEEK_ENABLED', 'false')
    monkeypatch.setattr(qwen_cloud.CloudVideoStore, 'from_environment', lambda: store)
    processing.recover_workspaces()
    assert db.execute("SELECT cleanup_state FROM media_assets WHERE asset_kind='ASR_AUDIO_TRANSFER'").fetchone() == ('REGISTERED',)
    assert processing.recover_workspaces()
    assert db.execute("SELECT cleanup_state FROM media_assets WHERE asset_kind='ASR_AUDIO_TRANSFER'").fetchone() == ('DELETED',)
    assert objects == {'another-owner-object'} and delete_calls == [upload_key, upload_key]


@pytest.mark.parametrize('interruption', ('cancel', 'lease_expired', 'maintenance'))
def test_deepseek_pending_response_does_not_publish_or_resend(bridge, monkeypatch, interruption):
    from app import deepseek_adapter, maintenance, pipeline
    from app.model_adapter import TranscriptSegment
    from test_deepseek_adapter import DelayedChatGateway, KEY

    db, context, owner, _, _ = bridge
    db.execute("UPDATE tasks SET requested_model='deepseek-v4.1-flash' WHERE id=%s", (context.id,))
    if interruption == 'maintenance':
        from app.deletion_control import _write

        # Seed the initial OPEN state for this fixture without replaying an owner restore.
        deployment_id = db.execute('SELECT deployment_id FROM deletion_control_state').fetchone()[0]
        gate = Path(os.environ['DELETION_JOURNAL_DIR']) / 'runtime' / 'maintenance.json'
        _write(gate, {'deployment_id': str(deployment_id), 'state': 'OPEN'})
        monkeypatch.setenv('MAINTENANCE_GATE_REQUIRED', 'true')
        assert maintenance.ready()

    processor = deepseek_adapter.DeepSeekModelAdapter(KEY)
    monkeypatch.setattr(processor, 'upload_audio', lambda *_: 'oss://fixture/audio.m4a')
    monkeypatch.setattr(processor, 'transcribe', lambda *_: (TranscriptSegment(.1, .5, 'Fixture'),))

    with DelayedChatGateway(3) as gateway, patch.object(
            deepseek_adapter.http.client, 'HTTPSConnection', gateway.connection):
        def interrupt():
            assert gateway.submitted.wait(3)
            if interruption == 'cancel':
                tasks.cancel_task(context.id, owner)
            elif interruption == 'lease_expired':
                db.execute("UPDATE tasks SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (context.id,))
            else:
                maintenance.set_state('CLOSED', db)

        trigger = Thread(target=interrupt)
        trigger.start()
        try:
            with pytest.raises((processing.ProcessingCancelled, AdapterError, maintenance.MaintenanceClosed)):
                pipeline.run_pipeline(context, processor)
            assert not gateway.responded.is_set(), 'Worker waited for the provider after interruption'
            assert gateway.responded.wait(4), 'The gateway did not attempt its late response'
            assert gateway.requests == 1
        finally:
            trigger.join(5)
            assert not trigger.is_alive()

    if interruption == 'maintenance':
        assert maintenance.read_state(db)['state'] == 'CLOSED'
    elif interruption == 'cancel':
        assert db.execute('SELECT status FROM tasks WHERE id=%s', (context.id,)).fetchone() == ('CANCELLED',)
    else:
        assert context.cancelled.is_set()
    assert db.execute('SELECT count(*) FROM task_results').fetchone() == (0,)
    assert db.execute('SELECT deepseek_phase FROM model_calls').fetchall() == [('SUMMARY_SUBMITTING',)]


def test_deepseek_summary_timeout_uses_worker_setting_and_does_not_resend(bridge, monkeypatch):
    from app import deepseek_adapter, pipeline
    from app.model_adapter import TranscriptSegment
    from test_deepseek_adapter import DelayedChatGateway, KEY

    db, context, _, _, _ = bridge
    db.execute("UPDATE tasks SET requested_model='deepseek-v4.1-flash' WHERE id=%s", (context.id,))
    monkeypatch.setenv('DEEPSEEK_SUMMARY_TIMEOUT_SECONDS', '1')
    processor = deepseek_adapter.DeepSeekModelAdapter(KEY)
    monkeypatch.setattr(processor, 'upload_audio', lambda *_: 'oss://fixture/audio.m4a')
    monkeypatch.setattr(processor, 'transcribe', lambda *_: (TranscriptSegment(.1, .5, 'Fixture'),))

    with DelayedChatGateway(3) as gateway, patch.object(
            deepseek_adapter.http.client, 'HTTPSConnection', gateway.connection):
        with pytest.raises(AdapterError) as error:
            pipeline.run_pipeline(context, processor)
        assert error.value.kind == 'timeout'
        assert not gateway.responded.is_set()
        assert gateway.requests == 1

    assert db.execute("SELECT status,failure_code FROM tasks").fetchone() == ('FAILED', 'MODEL_TIMEOUT')
    assert db.execute("SELECT status,deepseek_phase FROM model_calls").fetchone() == ('FAILED', 'SUMMARY_SUBMITTING')
    assert db.execute("SELECT count(*) FROM task_results").fetchone() == (0,)


@pytest.mark.parametrize('duration', (1003.762, 6953))
@pytest.mark.parametrize('ending', ('complete', 'midstream_eof', 'missing_done'))
def test_deepseek_stream_publishes_only_after_complete_response(bridge, monkeypatch, duration, ending):
    from dataclasses import replace

    from app import deepseek_adapter, pipeline
    from app.model_adapter import TranscriptSegment
    from test_deepseek_adapter import DelayedChatGateway, KEY, summary_stream

    monkeypatch.setenv('DEEPSEEK_ENABLED', 'true')
    db, context, owner, _, _ = bridge
    db.execute("UPDATE tasks SET requested_model='deepseek-v4.1-flash', duration_seconds=%s WHERE id=%s",
               (duration, context.id))
    prepare_media = processing.prepare_media

    def prepare_fixture(*args, **kwargs):
        return replace(prepare_media(*args, **kwargs), duration_seconds=duration)

    monkeypatch.setattr(processing, 'prepare_media', prepare_fixture)
    processor = deepseek_adapter.DeepSeekModelAdapter(KEY)
    monkeypatch.setattr(processor, 'upload_audio', lambda *_: 'oss://fixture/audio.m4a')
    monkeypatch.setattr(processor, 'transcribe', lambda *_: (TranscriptSegment(.1, .5, 'Fixture'),))

    complete = ending == 'complete'
    parts = summary_stream(done=complete)
    if ending == 'midstream_eof':
        parts = parts[:2]
    with DelayedChatGateway(0, stream_parts=parts) as gateway, patch.object(
            deepseek_adapter.http.client, 'HTTPSConnection', gateway.connection):
        if complete:
            pipeline.run_pipeline(context, processor)
        else:
            with pytest.raises(AdapterError) as error:
                pipeline.run_pipeline(context, processor)
            assert error.value.kind == 'unavailable'
        assert gateway.requests == 1
        assert gateway.payload['stream'] is True

    if complete:
        assert db.execute("SELECT language FROM task_results ORDER BY language").fetchall() == [('en',), ('zh',)]
        assert db.execute("SELECT status FROM tasks").fetchone() == ('SUCCEEDED',)
        assert db.execute("SELECT status,deepseek_phase,input_tokens,output_tokens FROM model_calls").fetchone() == (
            'SUCCEEDED', 'FINISHED', 23000, 9000)
    else:
        assert db.execute("SELECT count(*) FROM task_results").fetchone() == (0,)
        assert db.execute("SELECT status,failure_code FROM tasks").fetchone() == ('FAILED', 'MODEL_UNAVAILABLE')
        assert db.execute("SELECT status,deepseek_phase FROM model_calls").fetchone() == ('FAILED', 'SUMMARY_SUBMITTING')
        assert tasks.get_task(context.id, owner)['retry_reason'] == 'MODEL_REQUEST_UNCONFIRMED'


@pytest.mark.parametrize('outcome', ('no_words', 'service_failure', 'truncated'))
def test_deepseek_asr_and_summary_outcomes_at_publication(bridge, monkeypatch, outcome):
    from app import deepseek_adapter, pipeline
    from test_deepseek_adapter import AUDIO_URL, JOB, KEY, visual_candidates

    db, context, _, root, _ = bridge
    db.execute("UPDATE tasks SET requested_model='deepseek-v4.1-flash', duration_seconds=1 WHERE id=%s", (context.id,))
    processor = deepseek_adapter.DeepSeekModelAdapter(KEY)
    monkeypatch.setattr(processor, 'upload_audio', lambda *_: AUDIO_URL)
    requests = []

    def reply(host, method, path, headers, body, checkpoint, *, summary_deadline=None):
        checkpoint()
        requests.append((method,path))
        if path == deepseek_adapter.ASR_PATH:
            return {'output': {'task_id': JOB, 'task_status': 'PENDING'}}
        if method == 'GET':
            return {'output': {'task_id': JOB, 'task_status': 'FAILED',
                               'code': 'InternalError' if outcome == 'service_failure'
                               else 'ASR_RESPONSE_HAVE_NO_WORDS'}}
        assert path == deepseek_adapter.CHAT_PATH
        assert json.loads(json.loads(body)['messages'][1]['content'][0]['text'])['transcript'] == []
        return {'id': 'fixture-summary', 'model': 'deepseek-v4.1-flash',
                'choices': [{'finish_reason': 'length' if outcome == 'truncated' else 'stop',
                             'message': {'content': json.dumps(visual_candidates())}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 8192}}

    monkeypatch.setattr(deepseek_adapter, '_json_request', reply)
    if outcome == 'no_words':
        pipeline.run_pipeline(context, processor)
        results = db.execute('SELECT language,export_document FROM task_results ORDER BY language').fetchall()
        assert len(results) == 2
        for language, doc in results:
            assert deepseek_adapter.NO_SPEECH_NOTE[language] in doc['summary']
        assert db.execute('SELECT status FROM tasks WHERE id=%s', (context.id,)).fetchone() == ('SUCCEEDED',)
        assert db.execute('SELECT credits_available FROM users').fetchone() == (0,)
    else:
        with pytest.raises(AdapterError) as error:
            pipeline.run_pipeline(context, processor)
        expected = 'output_truncated' if outcome == 'truncated' else 'unavailable'
        assert error.value.kind == expected
        assert db.execute('SELECT status,failure_code FROM tasks').fetchone() == ('FAILED', 'MODEL_'+expected.upper())
        assert db.execute('SELECT count(*) FROM task_results').fetchone() == (0,)
        if outcome == 'truncated':
            assert db.execute('SELECT status,request_id,input_tokens,output_tokens FROM model_calls').fetchone() == (
                'FAILED', 'fixture-summary', 10, 8192)
    expected = [('POST', deepseek_adapter.ASR_PATH), ('GET', '/api/v1/tasks/'+JOB)]
    if outcome != 'service_failure':
        expected.append(('POST', deepseek_adapter.CHAT_PATH))
    assert requests == expected
    assert not list(root.iterdir())
