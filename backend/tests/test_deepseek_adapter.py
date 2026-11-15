"""Contract and cancellation checks for the synthetic DeepSeek gateway."""
from base64 import b64encode
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import socket
from threading import Event, Thread
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import deepseek_adapter as adapter
from app.model_adapter import AdapterError, ModelRequest, VisualEvidence, TranscriptSegment

KEY = 'sk-ws-synthetic-fixture'
JOB = 'synthetic-asr-task'
RESULT_HOST = 'dashscope-result-bj.oss-cn-beijing.aliyuncs.com'
RESULT_PATH = '/pre/filetrans-16k/fixture.json?Signature=synthetic'
UPLOAD_HOST = 'dashscope-file-fixture.oss-cn-beijing.aliyuncs.com'
UPLOAD_DIR = 'dashscope-instant/fixture/2026-11-12/probe'
AUDIO_URL = f'oss://{UPLOAD_DIR}/fixture.m4a'
AUDIO_BYTES = b'\x00\x00\x00\x1cftypM4A ' + b'synthetic' * 10000


def upload_policy(limit=1024**2, *, expires_in=300, host=UPLOAD_HOST):
    expiration = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    signed = {'expiration': expiration.isoformat(), 'conditions': [['content-length-range', 0, limit]]}
    return {'data': {'upload_host': 'https://' + host, 'upload_dir': UPLOAD_DIR,
                     'policy': b64encode(json.dumps(signed).encode()).decode(),
                     'signature': 'synthetic-signature', 'oss_access_key_id': 'synthetic-upload-id',
                     'x_oss_object_acl': 'private', 'x_oss_forbid_overwrite': 'true',
                     'expire_in_seconds': 300, 'max_file_size_mb': 1}}



def candidates():
    result = {}
    for language, words in (('zh', '蓝色画面和合成音'), ('en', 'Blue picture and a tone')):
        result[language] = {
            'language': language, 'summary': words, 'overview': words, 'points': [words] * 3,
            'chapters': [{'timestamp_seconds': 0, 'title': words}],
            'key_events': [{'timestamp_seconds': 0, 'title': words, 'description': words}],
            'limitations': ['Synthetic evidence'],
        }
    return result


def visual_candidates(frame_id=1):
    result = candidates()
    for candidate in result.values():
        for field in ('chapters', 'key_events'):
            for item in candidate[field]:
                del item['timestamp_seconds']
                item['frame_id'] = frame_id
    return result


class Reply(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status


class StreamReply(io.BytesIO):
    status = 200

    def getheader(self, name, default=None):
        return 'text/event-stream' if name.lower() == 'content-type' else default


class MockDeepSeekTransport:
    """Real adapter wire shape with synthetic replies; never opens sockets."""
    submissions = 0
    integration = False
    uploaded = None

    def __init__(self, host, timeout):
        assert host in (adapter.HOST, RESULT_HOST, UPLOAD_HOST) and timeout <= 8
        self.host = host
        self.sock = Mock()

    def request(self, method, path, body, headers):
        if self.host == UPLOAD_HOST:
            assert method == 'POST' and path == '/' and set(headers) == {'Content-Type', 'Content-Length'}
            chunks = list(body)
            assert len(chunks) > 2 and all(len(chunk) <= 65536 for chunk in chunks)
            contents = b''.join(chunks)
            assert int(headers['Content-Length']) == len(contents)
            message = BytesParser(policy=policy.default).parsebytes(
                ('Content-Type: ' + headers['Content-Type'] + '\r\n\r\n').encode() + contents)
            parts = list(message.iter_parts())
            fields = {part.get_param('name', header='Content-Disposition'): part.get_payload(decode=True).decode()
                      for part in parts[:-1]}
            assert fields['OSSAccessKeyId'] == 'synthetic-upload-id' and fields['Signature'] == 'synthetic-signature'
            assert fields['x-oss-object-acl'] == 'private' and fields['x-oss-forbid-overwrite'] == 'true'
            assert fields['success_action_status'] == '200'
            assert parts[-1].get_param('name', header='Content-Disposition') == 'file'
            audio = parts[-1].get_payload(decode=True)
            assert parts[-1].get_content_type() == 'audio/mp4' and audio[4:8] == b'ftyp'
            if not self.integration:
                assert audio == AUDIO_BYTES
            type(self).uploaded = 'oss://' + fields['key']
            if self.integration:
                print('S312_TEMP_AUDIO_VALIDATED', flush=True)
            self.reply = Reply({})
            return
        if self.host == RESULT_HOST:
            assert method == 'GET' and path == RESULT_PATH and not headers and body is None
            self.reply = Reply({'transcripts': [{'channel_id': 0, 'sentences': [
                {'begin_time': 100, 'end_time': 500, 'text': 'Synthetic transcript'},
            ]}]})
            return
        assert headers['Authorization'] == 'Bearer ' + KEY
        if path != adapter.ASR_PATH:
            assert 'X-DashScope-OssResourceResolve' not in headers
        if method == 'GET' and path.startswith('/api/v1/uploads?'):
            from urllib.parse import parse_qs, urlsplit
            assert parse_qs(urlsplit(path).query) == {'action': ['getPolicy'], 'model': [adapter.ASR_MODEL]}
            assert body is None and 'X-DashScope-OssResourceResolve' not in headers
            self.reply = Reply(upload_policy())
            return
        if method == 'POST' and path == adapter.ASR_PATH:
            data = json.loads(body)
            assert headers['X-DashScope-Async'] == 'enable'
            assert headers['X-DashScope-OssResourceResolve'] == 'enable'
            assert data['model'] == adapter.ASR_MODEL and data['parameters'] == {'channel_id': [0]}
            url, = data['input']['file_urls']
            assert url == type(self).uploaded and url.startswith(f'oss://{UPLOAD_DIR}/') and url.endswith('.m4a')
            type(self).submissions += 1
            if self.integration:
                print(f'S312_MOCK_VALIDATED {self.submissions}', flush=True)
            self.reply = Reply({'output': {'task_id': JOB, 'task_status': 'PENDING'}})
        elif method == 'GET' and path == '/api/v1/tasks/' + JOB:
            if self.integration and self.submissions == 2:
                time.sleep(7)
            self.reply = Reply({'output': {'task_id': JOB, 'task_status': 'SUCCEEDED', 'results': [
                {'subtask_status': 'SUCCEEDED', 'transcription_url': 'https://' + RESULT_HOST + RESULT_PATH},
            ]}})
        elif method == 'POST' and path == '/api/v1/tasks/' + JOB + '/cancel':
            self.reply = Reply({'output': {'task_id': JOB, 'task_status': 'CANCELED'}})
            if self.integration:
                print('S312_ASR_CANCELLED', flush=True)
        else:
            assert method == 'POST' and path == adapter.CHAT_PATH
            data = json.loads(body)
            assert data['model'] == 'deepseek-v4.1-flash' and 'effort' not in json.dumps(data)
            assert data['response_format'] == {'type': 'json_object'}
            assert data['stream'] is True and data['stream_options'] == {'include_usage': True}
            assert headers['Accept'] == 'text/event-stream'
            parts = data['messages'][1]['content']
            assert json.loads(parts[0]['text'])['transcript'][0][2] == 'Synthetic transcript'
            label = parts[1]['text']
            assert label.startswith('Frame 1 at ') and label.endswith(' seconds')
            timestamp = float(label.removeprefix('Frame 1 at ').removesuffix(' seconds'))
            assert 0 <= timestamp <= json.loads(parts[0]['text'])['duration_seconds']
            if not self.integration:
                assert timestamp == 0
            assert parts[2]['image_url']['url'].startswith('data:image/jpeg;base64,')
            self.reply = StreamReply(b''.join(summary_stream()))

    def getresponse(self):
        return self.reply

    def close(self):
        pass


def summary_stream(document=None, *, done=True, finish='stop'):
    def event(data):
        return b'data: ' + json.dumps(data, ensure_ascii=False).encode() + b'\r\n\r\n'

    text = json.dumps(candidates() if document is None else document, ensure_ascii=False)
    return (
        b': keep-alive\r\n\r\n' + event({
            'id': 'stream-fixture', 'model': 'deepseek-v4.1-flash',
            'choices': [{'index': 0, 'delta': {'reasoning_content': 'Synthetic reasoning'},
                         'finish_reason': None}], 'usage': None,
        }),
        event({'choices': [{'index': 0, 'delta': {'content': text[:24]}, 'finish_reason': None}]}),
        event({'choices': [{'index': 0, 'delta': {'content': text[24:]}, 'finish_reason': finish}]}),
        event({'choices': [], 'usage': {'prompt_tokens': 23000, 'completion_tokens': 9000}}),
        b'data: [DONE]\r\n\r\n' if done else b'',
    )


class DelayedChatGateway:
    """One local socket pair with real HTTP parsing and a controllable response."""

    def __init__(self, delay, *, stream_parts=None):
        self.client, self.server = socket.socketpair()
        self.delay = delay
        self.stream_parts = stream_parts
        self.sent_first_event = Event()
        self.submitted = Event()
        self.responded = Event()
        self.stop = Event()
        self.requests = 0
        self.reader = Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.reader.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.client.close()
        self.reader.join(3)
        self.server.close()
        assert not self.reader.is_alive()

    def connection(self, host, timeout):
        assert host == adapter.HOST and timeout == 8
        conn = adapter.http.client.HTTPConnection(host, timeout=timeout)
        conn.sock = self.client
        self.client.settimeout(timeout)
        return conn

    def serve(self):
        with self.server.makefile('rb') as reader:
            request = reader.readline()
            if not request:
                return
            assert request.startswith(b'POST /compatible-mode/v1/chat/completions ')
            self.requests += 1
            size = 0
            while (line := reader.readline()) != b'\r\n':
                assert line
                if line.lower().startswith(b'content-length:'):
                    size = int(line.split(b':', 1)[1])
            assert size
            self.payload = json.loads(reader.read(size))
            assert self.payload['model'] == 'deepseek-v4.1-flash'
            self.submitted.set()
            if self.stream_parts is None:
                if self.stop.wait(self.delay):
                    return
                self.send_stream(summary_stream(visual_candidates()), delay=0)
            else:
                self.send_stream(self.stream_parts, delay=self.delay)

    def send_stream(self, parts, *, delay):
        first, *remainder = parts
        headers = b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\nConnection: close\r\n\r\n'
        try:
            self.server.sendall(headers + first)
            self.sent_first_event.set()
            if not self.stop.wait(delay):
                for part in remainder:
                    self.server.sendall(part)
        except OSError:
            pass  # Expected if the Worker stopped waiting.
        finally:
            self.server.close()
            self.responded.set()


class DeepSeekTests(unittest.TestCase):
    def setUp(self):
        self.adapter = adapter.DeepSeekModelAdapter(KEY)
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.path = Path(workspace.name) / 'local-file.m4a'
        self.path.write_bytes(AUDIO_BYTES)
        self.audio = SimpleNamespace(start_seconds=1.25, end_seconds=3)
        self.phase = Mock()

    def test_transcript_pts_and_bilingual_payload(self):
        with patch.object(adapter.http.client, 'HTTPSConnection', MockDeepSeekTransport):
            url = self.adapter.upload_audio(self.path, lambda: True)
            segments = self.adapter.transcribe(url, self.audio, 3, lambda: True, self.phase)
            result = self.adapter.analyze(ModelRequest(3, (VisualEvidence(0, b'JPEG'),),
                                                       transcript=segments), lambda: True, self.phase)
        self.assertEqual(segments, (TranscriptSegment(1.35, 1.75, 'Synthetic transcript'),))
        self.assertEqual(set(result.localized_candidates), {'zh', 'en'})
        self.assertEqual([entry.args for entry in self.phase.call_args_list], [
            ('ASR_SUBMITTING',), ('ASR_WAITING', JOB), ('ASR_COMPLETE',), ('SUMMARY_SUBMITTING',), ('FINISHED',)])
        self.assertEqual(result.usage.output_tokens, 9000)

    def test_upload_limit_and_untrusted_destination_are_rejected_before_post(self):
        for document, kind in ((upload_policy(len(AUDIO_BYTES) + 5), 'evidence_budget'),
                               (upload_policy(host='example.invalid'), 'invalid_response')):
            with self.subTest(kind=kind), patch.object(adapter, '_json_request', return_value=document), \
                    patch.object(adapter.http.client, 'HTTPSConnection') as connection:
                with self.assertRaises(AdapterError) as raised:
                    self.adapter.upload_audio(self.path, lambda: True)
                self.assertEqual(raised.exception.kind, kind)
                connection.assert_not_called()

    def test_upload_refreshes_explicit_expiry_only_once(self):
        class Expired(io.BytesIO):
            status = 403

        class ExpiringTransport(MockDeepSeekTransport):
            posts = 0

            def request(self, method, path, body, headers):
                super().request(method, path, body, headers)
                if self.host == UPLOAD_HOST:
                    type(self).posts += 1
                    if type(self).posts == 1:
                        self.reply = Expired(b'<Error><Message>Invalid according to Policy: Policy expired.</Message></Error>')

        with patch.object(adapter.http.client, 'HTTPSConnection', ExpiringTransport):
            url = self.adapter.upload_audio(self.path, lambda: True)
        self.assertEqual(url, ExpiringTransport.uploaded)
        self.assertEqual(ExpiringTransport.posts, 2)

    def test_upload_lost_response_is_never_replayed(self):
        class LostResponse(MockDeepSeekTransport):
            posts = 0

            def getresponse(self):
                if self.host == UPLOAD_HOST:
                    type(self).posts += 1
                    raise TimeoutError
                return super().getresponse()

        with patch.object(adapter.http.client, 'HTTPSConnection', LostResponse):
            with self.assertLogs('app.qianwen_temp_upload', 'WARNING'), self.assertRaises(AdapterError) as raised:
                self.adapter.upload_audio(self.path, lambda: True)
        self.assertEqual(raised.exception.kind, 'timeout')
        self.assertEqual(LostResponse.posts, 1)

    def test_transcript_bounds_and_successful_silence(self):
        self.assertEqual(adapter._transcript({'transcripts': [{'channel_id': 0, 'sentences': []}]}, self.audio, 3), ())
        for start, end in ((-1, 100), (200, 100), (100, 3000), (True, 200)):
            with self.subTest(start=start, end=end), self.assertRaises(AdapterError):
                adapter._transcript({'transcripts': [{'channel_id': 0, 'sentences': [
                    {'begin_time': start, 'end_time': end, 'text': 'invalid'},
                ]}]}, self.audio, 3)

    def test_live_asr_result_path_is_accepted(self):
        path = '/prod/qwen-audio-3.1-asr-flash-filetrans/20261113/15%3A11/example-task-1.json'
        query = '?Expires=0&OSSAccessKeyId=fixture&Signature=synthetic'
        self.assertEqual(adapter._result_location('https://' + RESULT_HOST + path + query),
                         (RESULT_HOST, path + query))

    def test_untrusted_result_urls(self):
        for url in ('http://'+RESULT_HOST+RESULT_PATH, 'https://127.0.0.1/file',
                    'https://'+RESULT_HOST+'@example.invalid'+RESULT_PATH,
                    'https://'+RESULT_HOST+'/unrelated?Signature=synthetic',
                    'https://'+RESULT_HOST+'/prod/unrelated/20261113/15%3A11/file.json?Signature=synthetic',
                    'https://'+RESULT_HOST+'/prod/'+adapter.ASR_MODEL+'/20261113/15%3A11/file.json'):
            with self.subTest(url=url), self.assertRaises(AdapterError):
                adapter._result_location(url)

    def test_unknown_submission_keeps_phase_and_never_retries(self):
        with patch.object(adapter, '_json_request', side_effect=AdapterError('timeout')) as transport:
            with self.assertRaises(AdapterError):
                self.adapter.transcribe(AUDIO_URL, self.audio, 3, lambda: True, self.phase)
        transport.assert_called_once()
        self.phase.assert_called_once_with('ASR_SUBMITTING')
        self.phase.reset_mock()
        with patch.object(adapter, '_json_request', side_effect=adapter.ProviderStatus(429)) as transport:
            with self.assertRaises(AdapterError) as rejected:
                self.adapter.analyze(ModelRequest(3, (VisualEvidence(0, b'JPEG'),)), lambda: True, self.phase)
        transport.assert_called_once()
        self.assertEqual(rejected.exception.kind, 'rate_limit')
        self.assertEqual([entry.args for entry in self.phase.call_args_list], [('SUMMARY_SUBMITTING',), ('FINISHED',)])

    def test_summary_waits_for_a_response_delayed_more_than_eight_seconds(self):
        request = ModelRequest(3, (VisualEvidence(0, b'JPEG'),))
        with DelayedChatGateway(8.25) as gateway, \
                patch.object(adapter.http.client, 'HTTPSConnection', gateway.connection):
            result = self.adapter.analyze(request, lambda: None, self.phase)
        self.assertEqual(set(result.localized_candidates), {'zh', 'en'})
        self.assertEqual(gateway.requests, 1)
        self.assertEqual(self.phase.call_args_list[-1].args, ('FINISHED',))

    def test_summary_deadline_does_not_resend(self):
        request = ModelRequest(3, (VisualEvidence(0, b'JPEG'),), timeout_seconds=.2)
        with DelayedChatGateway(2) as gateway, \
                patch.object(adapter.http.client, 'HTTPSConnection', gateway.connection):
            with self.assertRaises(AdapterError) as failure:
                self.adapter.analyze(request, lambda: None, self.phase)
        self.assertEqual(failure.exception.kind, 'timeout')
        self.assertEqual(gateway.requests, 1)
        self.phase.assert_called_once_with('SUMMARY_SUBMITTING')

    def test_long_summary_reads_bilingual_sse_and_final_usage(self):
        request = ModelRequest(6953, (VisualEvidence(0, b'JPEG'),), transcript=(
            TranscriptSegment(0, 1, 'Fixture'),))
        with DelayedChatGateway(.01, stream_parts=summary_stream()) as gateway, patch.object(
                adapter.http.client, 'HTTPSConnection', gateway.connection):
            result = self.adapter.analyze(request, lambda: None, self.phase)
        self.assertEqual(result.localized_candidates, candidates())
        self.assertEqual((result.request_id, result.usage.input_tokens, result.usage.output_tokens),
                         ('stream-fixture', 23000, 9000))
        self.assertEqual(gateway.requests, 1)
        self.assertIs(gateway.payload['stream'], True)
        self.assertEqual(gateway.payload['stream_options'], {'include_usage': True})
        self.assertEqual(gateway.payload['response_format'], {'type': 'json_object'})
        self.assertIs(gateway.payload['enable_thinking'], True)
        self.assertEqual([call.args for call in self.phase.call_args_list],
                         [('SUMMARY_SUBMITTING',), ('FINISHED',)])

    def test_long_summary_preserves_confirmed_truncation_and_usage(self):
        request = ModelRequest(3600, (VisualEvidence(0, b'JPEG'),))
        with DelayedChatGateway(0, stream_parts=summary_stream(finish='length')) as gateway, patch.object(
                adapter.http.client, 'HTTPSConnection', gateway.connection):
            with self.assertRaises(AdapterError) as failure:
                self.adapter.analyze(request, lambda: None, self.phase)
        self.assertEqual((failure.exception.kind, failure.exception.request_id, failure.exception.usage.output_tokens),
                         ('output_truncated', 'stream-fixture', 9000))
        self.assertEqual(gateway.requests, 1)
        self.assertEqual(self.phase.call_args_list[-1].args, ('FINISHED',))

    def test_long_summary_deadline_still_applies_after_reasoning_event(self):
        request = ModelRequest(3600, (VisualEvidence(0, b'JPEG'),), timeout_seconds=.3)
        with DelayedChatGateway(2, stream_parts=summary_stream()) as gateway, patch.object(
                adapter.http.client, 'HTTPSConnection', gateway.connection):
            with self.assertRaises(AdapterError) as failure:
                self.adapter.analyze(request, lambda: None, self.phase)
            self.assertTrue(gateway.sent_first_event.is_set())
        self.assertEqual(failure.exception.kind, 'timeout')
        self.assertEqual(gateway.requests, 1)
        self.phase.assert_called_once_with('SUMMARY_SUBMITTING')

    def test_asr_empty_result_is_distinct_from_service_failure(self):
        url = AUDIO_URL
        ack = {'output': {'task_id': JOB, 'task_status': 'PENDING'}}
        for fields, no_words in (
                ({'code': 'ASR_RESPONSE_HAVE_NO_WORDS'}, True),
                ({'results': [{'subtask_status': 'FAILED', 'code': 'ASR_RESPONSE_HAVE_NO_WORDS'}]}, True),
                ({'code': 'ASR_RESPONSE_HAVE_NO_WORDS',
                  'results': [{'subtask_status': 'FAILED', 'code': 'FILE_DOWNLOAD_FAILED'}]}, False),
                ({'code': 'ASR_RESPONSE_HAVE_NO_WORDS',
                  'results': [{'subtask_status': 'FAILED'}, {'subtask_status': 'FAILED'}]}, True),
                ({'code': 'InternalError'}, False)):
            with self.subTest(fields=fields):
                self.phase.reset_mock()
                done = {'output': {'task_id': JOB, 'task_status': 'FAILED', **fields}}
                with patch.object(adapter, '_json_request', side_effect=[ack, done]) as http:
                    if no_words:
                        self.assertEqual(self.adapter.transcribe(url, self.audio, 3, lambda: None, self.phase), ())
                    else:
                        with self.assertRaises(AdapterError) as failure:
                            self.adapter.transcribe(url, self.audio, 3, lambda: None, self.phase)
                        self.assertEqual(failure.exception.kind, 'unavailable')
                self.assertEqual(http.call_count, 2)
                self.assertEqual(self.phase.call_args_list[-1].args, ('ASR_COMPLETE' if no_words else 'FINISHED',))

    def test_summary_failures_keep_metadata_and_length_is_explicit(self):
        valid = candidates()
        wrong_field = candidates()
        wrong_field['en']['points'] = []
        wrong_time = candidates()
        wrong_time['en']['chapters'][0]['timestamp_seconds'] = 1200
        wrong_language = candidates()
        wrong_language['en']['language'] = 'zh'
        request = ModelRequest(1003.762, (VisualEvidence(0, b'JPEG'),),
                               transcript=(TranscriptSegment(.1, .5, 'Fixture'),))
        for reason, content, used, expected, diagnostic in (
                ('length', '{"zh":', 32768, 'output_truncated', None),
                ('stop', '{"zh":', 32768, 'invalid_response', 'json'),
                ('stop', json.dumps({'zh': valid['zh']}), 20, 'invalid_response', 'language'),
                ('stop', json.dumps(wrong_field), 30, 'invalid_response', 'fields'),
                ('stop', json.dumps(wrong_language), 30, 'invalid_response', 'language'),
                ('stop', json.dumps(wrong_time), 30, 'invalid_response', 'timestamp'),
                ('stop', json.dumps(valid), 32768, None, None)):
            with self.subTest(reason=reason, diagnostic=diagnostic):
                raw = {'id': 'observed-call', 'model': 'deepseek-v4.1-flash',
                       'choices': [{'finish_reason': reason, 'message': {'content': content,
                                                                         'reasoning_content': 'private chain'}}],
                       'usage': {'prompt_tokens': 5801, 'completion_tokens': used}}
                with patch.object(adapter, '_json_request', return_value=raw) as http, \
                        patch.object(adapter.logger, 'warning') as log:
                    if expected:
                        with self.assertRaises(AdapterError) as failure:
                            self.adapter.analyze(request, lambda: None, self.phase)
                        self.assertEqual((failure.exception.kind, failure.exception.request_id,
                                          failure.exception.usage.output_tokens),
                                         (expected, 'observed-call', used))
                    else:
                        response = self.adapter.analyze(request, lambda: None, self.phase)
                        self.assertEqual(response.localized_candidates, valid)
                    if diagnostic:
                        payload = json.loads(log.call_args.args[1])
                        self.assertEqual(payload['reason'], diagnostic)
                        self.assertEqual(set(payload), {'reason', 'language', 'visual_only', 'request_id_digest'})
                    else:
                        log.assert_not_called()
                http.assert_called_once()
                submitted = json.loads(http.call_args.args[4])
                self.assertEqual(submitted['max_tokens'], 32768)
                self.assertIs(submitted['enable_thinking'], True)
                self.assertIs(submitted['stream'], True)
                self.assertNotIn('reasoning_effort', submitted)

    def test_visual_only_maps_frame_ids_and_rejects_invented_evidence(self):
        request = ModelRequest(30.526667, (VisualEvidence(0, b'JPEG'), VisualEvidence(12.34567, b'JPEG')))
        for frame_id in (2, 3, True, '2', None):
            raw = visual_candidates(frame_id)
            if frame_id is None:
                del raw['zh']['chapters'][0]['frame_id']
                raw['zh']['chapters'][0]['timestamp_seconds'] = 12.35
            reply = {'id': 'frame-only', 'model': 'deepseek-v4.1-flash',
                     'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(raw)}}],
                     'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}
            with self.subTest(frame_id=frame_id), patch.object(adapter, '_json_request', return_value=reply) as http:
                if type(frame_id) is not int or frame_id != 2:
                    with self.assertLogs(adapter.logger.name, level='WARNING') as logs, self.assertRaises(AdapterError):
                        self.adapter.analyze(request, lambda: None, self.phase)
                    self.assertIn('"reason": "visual_evidence"', logs.output[0])
                else:
                    response = self.adapter.analyze(request, lambda: None, self.phase)
                    for lang, item in response.localized_candidates.items():
                        self.assertIn(adapter.NO_SPEECH_NOTE[lang], item['summary'])
                        for field in ('chapters', 'key_events'):
                            self.assertEqual(item[field][0]['timestamp_seconds'], 12.34567)
                            self.assertNotIn('frame_id', item[field][0])
                submitted = json.loads(http.call_args.args[4])
                self.assertEqual(submitted['max_tokens'], 16384)
                self.assertIn('Frame 2 at', submitted['messages'][1]['content'][3]['text'])
                evidence = json.loads(submitted['messages'][1]['content'][0]['text'])
                self.assertEqual(evidence['transcript'], [])
                self.assertIn('Do not infer speech', ' '.join(evidence['limitations']))

    def test_output_budget_boundaries(self):
        raw = {'id': 'budget', 'model': 'deepseek-v4.1-flash',
               'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(visual_candidates())}}],
               'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}
        for seconds, budget in ((899.999, 16384), (900, 32768), (3599.999, 32768),
                                (3600, 49152), (7200, 49152)):
            with self.subTest(seconds=seconds), patch.object(adapter, '_json_request', return_value=raw) as http:
                self.adapter.analyze(ModelRequest(seconds, (VisualEvidence(0, b'JPEG'),)),
                                     lambda: None, self.phase)
                payload = json.loads(http.call_args.args[4])
                self.assertEqual(payload['max_tokens'], budget)
                self.assertIs(payload['stream'], True)
                self.assertEqual(payload['stream_options'], {'include_usage': True})

    def test_frame_budget_fails_before_network(self):
        frame = VisualEvidence(0, b'a' * (adapter.MAX_FRAME_BYTES + 1))
        with patch.object(adapter, '_json_request') as transport:
            with self.assertRaises(AdapterError) as raised:
                self.adapter.analyze(ModelRequest(3, (frame,)), lambda: True, self.phase)
        self.assertEqual(raised.exception.kind, 'evidence_budget')
        transport.assert_not_called()
        self.phase.assert_not_called()
