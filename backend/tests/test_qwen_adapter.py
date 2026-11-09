"""No-network contract tests and an injectable response for the isolated Compose run."""
import base64
import hashlib
import http.client
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from app.media import MAX_BYTES
from app.model_adapter import AdapterError, ModelRequest
from app.processing import _clean_cloud_asset
from app.qwen_adapter import HOST, PATH, QwenModelAdapter
from app.qwen_cloud import CloudVideoStore
from app.worker import _configured_processor


KEY = 'sk-ws-synthetic-fixture'
MEDIA = b'fixture-video-with-audio'
CLOUD_HOST = 'fixture-bucket.oss-cn-hangzhou.aliyuncs.com'
TASK_ID = '10000000-0000-4000-8000-000000000001'
REQUEST_ID = '20000000-0000-4000-8000-000000000002'
PRIVATE_URL = f'https://{CLOUD_HOST}/vsa-qwen/video?X-Oss-Signature=secret-fixture'


def stream_fixture():
    bilingual = {}
    for language in ('zh', 'en'):
        words = '蓝色画面和合成音' if language == 'zh' else 'Blue picture and a tone'
        bilingual[language] = dict(language=language, summary=words, overview=words,
            points=[words, words, words], chapters=[dict(timestamp_seconds=0, title=words)],
            key_events=[dict(timestamp_seconds=1, title=words, description=words)], limitations=[words])
    output = json.dumps(bilingual)
    chunks = [
        dict(id='chatcmpl-fixture', choices=[dict(delta=dict(content=output[:100]))]),
        dict(id='chatcmpl-fixture', choices=[dict(delta=dict(content=output[100:]), finish_reason='stop')]),
        dict(id='chatcmpl-fixture', choices=[], usage=dict(prompt_tokens=270, completion_tokens=21)),
    ]
    return b''.join(b'data: ' + json.dumps(chunk).encode() + b'\n\n' for chunk in chunks) + b'data: [DONE]\n\n'


class Response(io.BytesIO):
    status = 200

    def getheader(self, key, default=''):
        return 'text/event-stream' if key.lower() == 'content-type' else default


class MockTransport:
    """Use only in tests. Any second request on this transport fails."""
    def __init__(self, host, timeout):
        assert host == HOST and timeout > 0
        self.requests = 0

    def request(self, method, path, body, headers):
        self.requests += 1
        assert self.requests == 1 and method == 'POST' and path == PATH
        assert headers['Authorization'] == 'Bearer ' + KEY
        payload = json.loads(body)
        media = payload['messages'][0]['content'][0]['video_url']['url']
        digest = os.getenv('S3_11_FIXTURE_SHA256')
        if os.getenv('S3_11_FIXTURE_CLOUD'):
            assert media.startswith(f'https://{CLOUD_HOST}/vsa-qwen/')
            assert media.endswith('?X-Oss-Signature=fixture')
        else:
            decoded = base64.b64decode(media.removeprefix('data:;base64,'), validate=True)
            if digest:
                assert hashlib.sha256(decoded).hexdigest() == digest
            else:
                assert decoded == MEDIA
        assert payload['model'] == 'qwen3.8-omni-flash'
        assert payload['reasoning_effort'] == 'none' and payload['enable_search'] is False
        assert payload['stream'] and payload['stream_options']['include_usage']

    def getresponse(self):
        return Response(stream_fixture())

    def close(self):
        pass


class QwenTests(unittest.TestCase):
    def setUp(self):
        self.adapter = QwenModelAdapter(KEY)
        self.request = ModelRequest(3, (), open_video=lambda: io.BytesIO(MEDIA), task_id=TASK_ID)

    def test_bilingual_video_and_usage(self):
        with patch('app.qwen_adapter.http.client.HTTPSConnection', MockTransport):
            result = self.adapter.analyze(self.request, lambda: True)
        self.assertEqual(set(result.localized_candidates), {'zh', 'en'})
        self.assertEqual(result.request_id, 'chatcmpl-fixture')
        self.assertEqual(result.usage.input_tokens, 270)
        self.assertEqual(result.usage.output_tokens, 21)

    def test_truncated_response_preserves_usage(self):
        class Truncated(MockTransport):
            def getresponse(self):
                return Response(stream_fixture().replace(b'data: [DONE]\n\n', b''))
        with patch('app.qwen_adapter.http.client.HTTPSConnection', Truncated):
            with self.assertRaises(AdapterError) as raised:
                self.adapter.analyze(self.request)
        self.assertEqual(raised.exception.kind, 'invalid_response')
        self.assertEqual(raised.exception.usage.input_tokens, 270)

    def test_over_limit_or_cancellation_never_connects(self):
        self.adapter.max_inline_video_bytes = len(MEDIA) - 1
        with patch('app.qwen_adapter.http.client.HTTPSConnection') as transport:
            with self.assertRaises(AdapterError) as oversized:
                self.adapter.analyze(self.request)
            with self.assertRaises(AdapterError) as cancelled:
                self.adapter.analyze(self.request, lambda: False)
            transport.assert_not_called()
        self.assertEqual(oversized.exception.kind, 'inline_video_too_large')
        self.assertEqual(cancelled.exception.kind, 'cancelled')

    def test_provider_statuses_do_not_retry(self):
        for status, kind in ((401, 'auth'), (429, 'rate_limit'), (413, 'input'), (503, 'unavailable')):
            with self.subTest(status=status):
                class ErrorTransport(MockTransport):
                    def getresponse(self):
                        response = Response()
                        response.status = status
                        return response
                with patch('app.qwen_adapter.http.client.HTTPSConnection', side_effect=ErrorTransport) as transport:
                    with self.assertRaises(AdapterError) as raised:
                        self.adapter.analyze(self.request)
                    self.assertEqual(raised.exception.kind, kind)
                    transport.assert_called_once()

    def failure_log(self, transport, request=None):
        with patch('app.qwen_adapter.http.client.HTTPSConnection', side_effect=transport) as connection:
            with self.assertLogs('app.qwen_adapter', level='WARNING') as logs:
                with self.assertRaises(AdapterError) as raised:
                    self.adapter.analyze(request or self.request)
            connection.assert_called_once()
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn(KEY, logs.output[0])
        self.assertNotIn(PRIVATE_URL, logs.output[0])
        self.assertNotIn('private fixture content', logs.output[0])
        return raised.exception, json.loads(logs.output[0].split('QWEN_CALL_FAILED ', 1)[1])

    def test_http_failure_records_status_known_code_and_body_request_id(self):
        class JsonResponse(Response):
            status = 503

            def getheader(self, key, default=''):
                return 'application/json' if key.lower() == 'content-type' else default

        class JsonFailure(MockTransport):
            def getresponse(self):
                return JsonResponse(json.dumps({
                    'error': {'code': 'ServiceUnavailable', 'message': f'{KEY} {PRIVATE_URL} private fixture content'},
                    'request_id': REQUEST_ID,
                }).encode())

        error, entry = self.failure_log(JsonFailure)
        self.assertEqual(error.kind, 'unavailable')
        self.assertEqual(error.request_id, REQUEST_ID)
        self.assertEqual(entry['task_id'], TASK_ID)
        self.assertEqual(entry['http_status'], 503)
        self.assertEqual(entry['provider_code'], 'ServiceUnavailable')
        self.assertEqual(entry['request_id'], REQUEST_ID)
        self.assertEqual(entry['stage'], 'http_error')
        self.assertGreaterEqual(entry['elapsed_ms'], 0)

    def test_stream_failure_records_oss_download_error_without_signed_url(self):
        class StreamFailure(MockTransport):
            def request(self, method, path, body, headers):
                self.requests += 1
                self.payload = json.loads(body)
                assert self.payload['messages'][0]['content'][0]['video_url']['url'] == PRIVATE_URL

            def getresponse(self):
                data = {'error': {'code': 'InvalidURL.Timeout', 'message': PRIVATE_URL, 'requestId': REQUEST_ID}}
                return Response(b'data: ' + json.dumps(data).encode() + b'\n\n')

        self.adapter.cloud = SimpleNamespace(host=CLOUD_HOST)
        error, entry = self.failure_log(StreamFailure, ModelRequest(3, (), video_url=PRIVATE_URL, task_id=TASK_ID))
        self.assertEqual(error.kind, 'unavailable')
        self.assertEqual(entry['input_mode'], 'oss')
        self.assertEqual(entry['stage'], 'stream_error')
        self.assertEqual(entry['http_status'], 200)
        self.assertEqual(entry['provider_code'], 'InvalidURL.Timeout')
        self.assertEqual(entry['request_id'], REQUEST_ID)

    def test_error_envelope_omits_unrecognized_codes_and_untrusted_identifiers(self):
        class Untrusted(MockTransport):
            def getresponse(self):
                payload = {'id': KEY, 'error': {'code': PRIVATE_URL, 'message': 'private fixture content'}}
                return Response(b'data: ' + json.dumps(payload).encode() + b'\n\n')

        error, entry = self.failure_log(Untrusted)
        self.assertEqual(entry['provider_code'], 'unrecognized')
        self.assertIsNone(entry['request_id'])
        self.assertIsNone(error.request_id)

    def test_disconnect_while_waiting_for_headers_has_no_http_status(self):
        class Disconnected(MockTransport):
            def getresponse(self):
                raise http.client.RemoteDisconnected(f'{KEY} {PRIVATE_URL}')

        error, entry = self.failure_log(Disconnected)
        self.assertEqual(error.kind, 'unavailable')
        self.assertEqual(entry['stage'], 'await_headers')
        self.assertEqual(entry['transport_error'], 'remote_disconnected')
        self.assertIsNone(entry['http_status'])
        self.assertIsNone(entry['request_id'])

    def test_mode_requires_both_switches_and_valid_key(self):
        with patch.dict(os.environ, {'WORKER_PROCESSOR_MODE': 'qwen', 'MODEL_CALLS_ENABLED': 'false'}, clear=True):
            with self.assertRaises(ValueError):
                _configured_processor()
            os.environ['MODEL_CALLS_ENABLED'] = 'true'
            with self.assertRaises(ValueError):
                _configured_processor()
            os.environ['DASHSCOPE_API_KEY'] = KEY
            self.assertIsInstance(_configured_processor().keywords['adapter'], QwenModelAdapter)
        with patch.dict(os.environ, {'WORKER_PROCESSOR_MODE': 'offline', 'DASHSCOPE_API_KEY': KEY}, clear=True):
            self.assertEqual(_configured_processor().__name__, 'run_pipeline')


class MockCloudStore:
    host = CLOUD_HOST
    bucket = 'fixture-bucket'
    backend = 'S3:' + 'f' * 64

    def check_bucket(self):
        pass

    def asset_key(self, task_id, filename):
        return f'vsa-qwen/{task_id}/{uuid4().hex}.mp4'

    def upload(self, path, key, checkpoint):
        checkpoint()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == os.environ['S3_11_FIXTURE_SHA256']
        return f'https://{self.host}/{key}?X-Oss-Signature=fixture'

    def remove(self, asset):
        assert asset['storage_key'].startswith(f"vsa-qwen/{asset['task_id']}/")
        assert asset['storage_bucket'] == self.bucket and asset['storage_backend'] == self.backend


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.store = CloudVideoStore('cn-hangzhou', 'fixture-bucket', 'fixture-id', 'fixture-secret')
        self.task_id = uuid4()
        self.key = self.store.asset_key(self.task_id, 'video.mov')
        gate = patch.dict(os.environ, {'MAINTENANCE_GATE_REQUIRED': 'false'})
        gate.start()
        self.addCleanup(gate.stop)

    def test_signs_get_for_two_gb_without_loading_file_into_memory(self):
        def upload(request, path):
            self.assertEqual(request.content_length, MAX_BYTES)
            self.assertEqual(request.acl, 'private')
            self.assertEqual(request.content_type, 'video/quicktime')
            self.assertEqual(Path(path).stat().st_size, MAX_BYTES)

        with TemporaryDirectory() as temp, patch.object(self.store.client, 'put_object_from_file', side_effect=upload):
            path = Path(temp) / 'sparse'
            with path.open('wb') as stream:
                stream.truncate(MAX_BYTES)
            parsed = urlsplit(self.store.upload(path, self.key, lambda: True))
        self.assertEqual(parsed.netloc, CLOUD_HOST)
        self.assertEqual(parsed.path, '/' + self.key)
        query = parse_qs(parsed.query)
        self.assertIn(int(query['x-oss-expires'][0]), (899, 900))
        self.assertIn('x-oss-signature', query)

    def test_refuses_public_versioned_and_unprotected_buckets(self):
        self.store.client = Mock()
        client = self.store.client
        client.get_bucket_versioning.return_value.version_status = None
        client.get_bucket_acl.return_value.acl = 'private'
        public_access = SimpleNamespace(block_public_access=True)
        client.get_bucket_public_access_block.return_value.public_access_block_configuration = public_access
        rule = SimpleNamespace(status='Enabled', prefix='vsa-qwen/', tags=None, filter=None,
                               expiration=SimpleNamespace(days=1))
        client.get_bucket_lifecycle.return_value.lifecycle_configuration.rules = [rule]
        self.store.check_bucket()
        for obj, field, value in ((rule.expiration, 'days', 2),
                                  (public_access, 'block_public_access', False),
                                  (client.get_bucket_acl.return_value, 'acl', 'public-read'),
                                  (client.get_bucket_versioning.return_value, 'version_status', 'Suspended')):
            with patch.object(obj, field, value), self.assertRaises(ValueError):
                self.store.check_bucket()

    def test_delete_confirms_absence_and_refuses_another_task(self):
        self.store.client = Mock()
        self.store.client.get_bucket_versioning.return_value.version_status = None
        error = self.store.oss.exceptions.ServiceError(
            status_code=404, code='NoSuchKey', request_id='', message='', ec='', timestamp='', request_target='')
        self.store.client.head_object.side_effect = self.store.oss.exceptions.OperationError(name='HeadObject', error=error)
        asset = dict(task_id=self.task_id, storage_key=self.key, storage_bucket=self.store.bucket,
                     storage_backend=self.store.backend, storage_version=None)
        self.store.remove(asset)
        with self.assertRaises(ValueError):
            self.store.remove(dict(asset, task_id=uuid4()))
        self.store.client.delete_object.assert_called_once()
        self.assertEqual(self.store.client.get_bucket_versioning.call_count, 2)

    def test_failed_cleanup_keeps_locator_for_recovery(self):
        cloud = Mock()
        cloud.remove.side_effect = (OSError('simulated outage'), None)
        asset = {'id': uuid4()}
        with patch('app.processing.tasks.connect') as connect:
            with self.assertLogs('app.processing', level='WARNING'):
                self.assertFalse(_clean_cloud_asset(asset, cloud))
            connect.assert_not_called()
            self.assertTrue(_clean_cloud_asset(asset, cloud))
            connection = connect.return_value.__enter__.return_value
            self.assertEqual(connection.execute.call_args.args[1], (asset['id'],))


if __name__ == '__main__':
    unittest.main()
