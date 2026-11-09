"""Qianwen AI Chat Completions with inline or private staged video."""
import base64
import http.client
import json
import logging
import math
import os
import re
import socket
import ssl
import time

from app import results
from app.model_adapter import AdapterError, ModelResponse, TokenUsage, validate_usage
from app.media import MAX_DURATION


HOST = 'maas.qianwenaiapi.com'
PATH = '/compatible-mode/v1/chat/completions'
BASE_URL = f'https://{HOST}/compatible-mode/v1'
MODEL = 'qwen3.8-omni-flash'
DATA_PREFIX = 'data:;base64,'
# Stay below the documented 10 MB encoded-video boundary, including the prefix.
MAX_INLINE_VIDEO_BYTES = ((10_000_000 - len(DATA_PREFIX) - 1) // 4) * 3
MAX_OUTPUT_TOKENS = 8192
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ID_PATTERN = r'[A-Za-z0-9_.:-]{1,128}'
UUID_PATTERN = r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}'
MAX_ERROR_BYTES = 4096
PROVIDER_CODES = frozenset({
    'AccessDenied', 'Arrearage', 'BadRequest.InputDownloadFailed', 'BadRequest.TooLarge',
    'BadRequest.UnsupportedFileFormat', 'ClientDisconnect', 'DataInspectionFailed', 'InternalError',
    'InternalError.Algo.InvalidParameter', 'InvalidApiKey', 'InvalidFile.DownloadFailed',
    'InvalidFile.Duration', 'InvalidFile.Format', 'InvalidFile.Size', 'InvalidInputLength',
    'InvalidParameter', 'InvalidURL', 'InvalidURL.ConnectionRefused', 'InvalidURL.Timeout',
    'ModelNotFound', 'NotFound', 'RequestTimeout', 'ServiceUnavailable', 'ServiceUnavailableError',
    'Throttling', 'Throttling.AllocationQuota', 'Throttling.Concurrency', 'Throttling.RateQuota',
    'access_denied', 'context_length_exceeded', 'data_inspection_failed', 'insufficient_quota',
    'internal_error', 'invalid_api_key', 'invalid_request_error', 'rate_limit_exceeded', 'server_error',
})
logger = logging.getLogger(__name__)


def _diagnostic_id(value):
    if isinstance(value, str) and re.fullmatch(r'(?:chatcmpl-)?' + UUID_PATTERN, value):
        return value
    return None


def _error_metadata(payload):
    if not isinstance(payload, dict):
        return None, None
    nested = payload.get('error')
    if not isinstance(nested, dict):
        nested = {}
    code = nested.get('code') or payload.get('code')
    if code is not None and (not isinstance(code, str) or code not in PROVIDER_CODES):
        code = 'unrecognized'
    for source in (payload, nested):
        for key in ('request_id', 'requestId', 'requestID', 'id'):
            identifier = _diagnostic_id(source.get(key))
            if identifier:
                return code, identifier
    return code, None


def _http_error_metadata(response, sock, deadline):
    # A single bounded read can recover small JSON error envelopes. A missing
    # or incomplete body must never change the original failure classification.
    if not response.getheader('Content-Type', '').lower().startswith('application/json'):
        return None, None
    try:
        if sock is not None:
            sock.settimeout(min(1.0, max(0.001, deadline - time.monotonic())))
        data = response.read1(MAX_ERROR_BYTES + 1)
        if len(data) <= MAX_ERROR_BYTES:
            return _error_metadata(json.loads(data))
    except (OSError, http.client.HTTPException, ValueError, RecursionError):
        pass
    return None, None


def _transport_kind(error):
    for error_type, label in (
        (socket.gaierror, 'dns'), (ssl.SSLError, 'tls'), (TimeoutError, 'socket_timeout'),
        (http.client.RemoteDisconnected, 'remote_disconnected'), (ConnectionResetError, 'connection_reset'),
        (ConnectionRefusedError, 'connection_refused'), (BrokenPipeError, 'broken_pipe'),
        (http.client.HTTPException, 'http_protocol'),
    ):
        if isinstance(error, error_type):
            return label
    return 'network_io'


def _provider_error(status):
    if status in (401, 403):
        return 'auth'
    if status == 429:
        return 'rate_limit'
    if status == 408:
        return 'timeout'
    if status in (400, 413, 415, 422):
        return 'input'
    return 'unavailable'


def _usage(value):
    details = value.get('prompt_tokens_details') or {}
    result = TokenUsage(value['prompt_tokens'], value['completion_tokens'], details.get('cached_tokens'))
    validate_usage(result)
    if result.input_tokens is None or result.output_tokens is None:
        raise AdapterError('invalid_response')
    if result.cached_tokens is not None and result.cached_tokens > result.input_tokens:
        raise AdapterError('invalid_response')
    return result


class QwenModelAdapter:
    model_version = MODEL
    adapter_version = 'qwen-video-v2'
    prompt_version = 'qwen-bilingual-v1'
    max_inline_video_bytes = MAX_INLINE_VIDEO_BYTES

    def __init__(self, api_key, cloud=None):
        key = api_key.strip()
        prefix = 'sk-ws-' if key.startswith('sk-ws-') else 'sk-'
        if key.startswith('sk-sp-') or not key.startswith(prefix) or not re.fullmatch(
            r'[A-Za-z0-9._~+/-]+=*', key[len(prefix):]
        ):
            raise ValueError('A Qianwen pay-as-you-go API key is required')
        self._api_key = key
        self.cloud = cloud

    @classmethod
    def from_environment(cls):
        if os.getenv('DASHSCOPE_BASE_URL', BASE_URL).rstrip('/') != BASE_URL or os.getenv('QWEN_MODEL', MODEL) != MODEL:
            raise ValueError('Unsupported Qianwen endpoint or model')
        from app.qwen_cloud import CloudVideoStore
        return cls(os.getenv('DASHSCOPE_API_KEY', ''), CloudVideoStore.from_environment())

    def _inline_url(self, open_video, check):
        video = bytearray()
        with open_video() as stream:
            while True:
                check()
                chunk = stream.read(min(1024 * 1024, self.max_inline_video_bytes - len(video) + 1))
                if not chunk:
                    break
                video.extend(chunk)
                if len(video) > self.max_inline_video_bytes:
                    raise AdapterError('inline_video_too_large')
        if not video:
            raise AdapterError('input')
        return DATA_PREFIX + base64.b64encode(video).decode('ascii')

    def analyze(self, request, checkpoint=None):
        duration = request.duration_seconds
        if ((request.open_video is None) == (request.video_url is None)
                or not math.isfinite(duration) or not 0 < duration <= MAX_DURATION
                or not math.isfinite(request.timeout_seconds) or request.timeout_seconds <= 0):
            raise AdapterError('input')
        deadline = time.monotonic() + request.timeout_seconds

        def check():
            if checkpoint is not None and checkpoint() is False:
                raise AdapterError('cancelled')
            if time.monotonic() >= deadline:
                raise AdapterError('timeout')

        video_url = request.video_url
        if video_url is None:
            video_url = self._inline_url(request.open_video, check)
        elif self.cloud is None or not video_url.startswith(f'https://{self.cloud.host}/vsa-qwen/'):
            raise AdapterError('input')

        prompt = (
            f'Analyze the video picture and its audio together. Duration is {duration:.3f} seconds. '
            'Return only a JSON object keyed zh and en with equivalent Chinese and English summaries. '
            'Each value has exactly language (zh or en matching its key), summary, overview, points '
            '(3 to 7 nonempty strings), chapters (at least one object with timestamp_seconds and title), '
            'key_events (objects with timestamp_seconds, title, description), and limitations '
            '(a list of nonempty strings). Use numeric timestamps from the start of the original video, '
            'in ascending order and within duration. If there are fewer than three defensible events, '
            'explain why in limitations. Describe missing audio or uncertain evidence without inventing it. '
            'Omit the input URL and its parameters from the output.'
        )
        content = [
            {'type': 'video_url', 'video_url': {'url': video_url}},
            {'type': 'text', 'text': prompt},
        ]
        body = json.dumps({
            'model': MODEL, 'messages': [{'role': 'user', 'content': content}],
            'modalities': ['text'], 'stream': True, 'stream_options': {'include_usage': True},
            'reasoning_effort': 'none', 'enable_search': False, 'max_tokens': MAX_OUTPUT_TOKENS,
            'response_format': {'type': 'json_object'},
        }).encode('utf-8')
        request_id = None
        diagnostic_id = None
        provider_code = None
        http_status = None
        usage = None
        conn = None
        stage = 'connect_or_send'
        started = time.monotonic()

        def failure(kind, transport_error=None):
            if kind != 'cancelled':
                task_id = request.task_id
                logger.warning('QWEN_CALL_FAILED %s', json.dumps({
                    'task_id': task_id if isinstance(task_id, str) and re.fullmatch(UUID_PATTERN, task_id) else None,
                    'model': MODEL, 'input_mode': 'oss' if request.video_url else 'inline',
                    'stage': stage, 'kind': kind, 'elapsed_ms': round((time.monotonic() - started) * 1000),
                    'http_status': http_status, 'provider_code': provider_code,
                    'request_id': diagnostic_id or _diagnostic_id(request_id),
                    'transport_error': transport_error,
                }, separators=(',', ':')))
            return AdapterError(kind, request_id=diagnostic_id or _diagnostic_id(request_id), usage=usage)

        try:
            check()
            conn = http.client.HTTPSConnection(HOST, timeout=min(30, deadline - time.monotonic()))
            check()
            conn.request('POST', PATH, body, {
                'Authorization': 'Bearer ' + self._api_key, 'Content-Type': 'application/json',
                'Accept': 'text/event-stream',
            })
            stage = 'await_headers'
            check()
            # Keep the socket reference even if HTTPConnection detaches it
            # for a response that ends by closing the connection.
            sock = getattr(conn, 'sock', None)
            if sock is not None:
                sock.settimeout(max(0.001, deadline - time.monotonic()))
            with conn.getresponse() as response:
                http_status = response.status
                header_id = response.getheader('x-request-id', '')
                if re.fullmatch(ID_PATTERN, header_id):
                    request_id = header_id
                diagnostic_id = _diagnostic_id(header_id) or _diagnostic_id(response.getheader('x-dashscope-request-id'))
                check()
                if response.status != 200:
                    stage = 'http_error'
                    provider_code, body_id = _http_error_metadata(response, sock, deadline)
                    diagnostic_id = diagnostic_id or body_id
                    raise AdapterError(_provider_error(response.status))
                stage = 'read_stream'
                if not response.getheader('Content-Type', '').lower().startswith('text/event-stream'):
                    raise AdapterError('invalid_response')
                text, finish, done, received = [], None, False, 0
                while True:
                    check()
                    if sock is not None:
                        sock.settimeout(max(0.001, deadline - time.monotonic()))
                    line = response.readline(131_073)
                    check()
                    if not line:
                        break
                    received += len(line)
                    if len(line) > 131_072 or received > MAX_RESPONSE_BYTES:
                        raise AdapterError('invalid_response')
                    if not line.startswith(b'data:'):
                        continue
                    data = line[5:].strip()
                    if data == b'[DONE]':
                        done = True
                        break
                    event = json.loads(data)
                    if event.get('error'):
                        stage = 'stream_error'
                        provider_code, body_id = _error_metadata(event)
                        diagnostic_id = diagnostic_id or body_id
                        raise AdapterError('unavailable')
                    identifier = event.get('id')
                    if request_id is None and isinstance(identifier, str) and re.fullmatch(ID_PATTERN, identifier):
                        request_id = identifier
                    if event.get('usage') is not None:
                        usage = _usage(event['usage'])
                    for choice in event.get('choices') or []:
                        delta = choice.get('delta') or {}
                        if delta.get('tool_calls') or delta.get('reasoning_content'):
                            raise AdapterError('invalid_response')
                        if isinstance(delta.get('content'), str):
                            text.append(delta['content'])
                        if choice.get('finish_reason') is not None:
                            finish = choice['finish_reason']
                if not done or finish != 'stop' or not request_id or usage is None:
                    raise AdapterError('invalid_response')
                stage = 'validate_result'
                output = ''.join(text)
                if request.video_url and 'x-oss-signature' in output.lower():
                    raise AdapterError('invalid_response')
                localized = results._parse(output)
                if not isinstance(localized, dict) or set(localized) != {'zh', 'en'}:
                    raise AdapterError('invalid_response')
                for language, candidate in localized.items():
                    results.validate_output(candidate, 'video.mp4', duration, language)
                return ModelResponse(localized[request.language], (), request_id, usage, MODEL,
                                     self.adapter_version, localized)
        except AdapterError as error:
            raise failure(error.kind) from None
        except TimeoutError as error:
            raise failure('timeout', _transport_kind(error)) from None
        except (OSError, http.client.HTTPException) as error:
            raise failure('unavailable', _transport_kind(error)) from None
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise failure('invalid_response') from None
        finally:
            if conn is not None:
                conn.close()
