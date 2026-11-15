"""Qianwen's provider-expiring upload, separate from app-owned OSS assets."""
import base64
from datetime import datetime, timezone
import http.client
import json
import logging
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4
from xml.etree import ElementTree

from app.model_adapter import AdapterError


logger = logging.getLogger(__name__)
UPLOAD_SECONDS = 300
CHUNK_BYTES = 64 * 1024
POLICY_FIELDS = {
    'OSSAccessKeyId': 'oss_access_key_id',
    'policy': 'policy',
    'Signature': 'signature',
    'x-oss-object-acl': 'x_oss_object_acl',
    'x-oss-forbid-overwrite': 'x_oss_forbid_overwrite',
}


def _upload_policy(reply):
    try:
        data = reply['data']
        host = urlsplit(data['upload_host'])
        if (host.scheme != 'https'
                or not re.fullmatch(r'dashscope-file(?:-[a-z0-9-]+)?\.oss-cn-[a-z0-9-]+\.aliyuncs\.com', host.netloc)
                or host.path not in ('', '/') or host.query or host.fragment):
            raise ValueError
        directory = data['upload_dir']
        if not re.fullmatch(r'dashscope-instant(?:/[A-Za-z0-9_-]+)+', directory):
            raise ValueError
        fields = {name: data[source] for name, source in POLICY_FIELDS.items()}
        if (any(not isinstance(value, str) or not re.fullmatch(r'[!-~]{1,16384}', value)
                for value in fields.values())
                or fields['x-oss-object-acl'] != 'private' or fields['x-oss-forbid-overwrite'] != 'true'):
            raise ValueError
        limit, seconds = data['max_file_size_mb'], data['expire_in_seconds']
        if type(limit) is not int or limit <= 0 or type(seconds) is not int or seconds <= 0:
            raise ValueError
        policy = json.loads(base64.b64decode(fields['policy'], validate=True))
        expires = datetime.fromisoformat(policy['expiration'].replace('Z', '+00:00'))
        if expires.tzinfo is None:
            raise ValueError
        (minimum, maximum), = [condition[1:] for condition in policy['conditions']
                              if isinstance(condition, list) and condition[:1] == ['content-length-range']]
        if type(minimum) is not int or type(maximum) is not int or not 0 <= minimum < maximum:
            raise ValueError
    except (KeyError, AttributeError, TypeError, ValueError, RecursionError):
        raise AdapterError('invalid_response') from None
    return host.netloc, directory, fields, seconds, expires, minimum, maximum, limit * 1024**2


def _multipart(audio, fields, boundary, check):
    for key, value in fields.items():
        yield (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
               f'{value}\r\n').encode('ascii')
    yield (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.m4a"\r\n'
           'Content-Type: audio/mp4\r\n\r\n').encode('ascii')
    if audio is not None:
        while True:
            check()
            chunk = audio.read(CHUNK_BYTES)
            if not chunk:
                break
            yield chunk
    yield f'\r\n--{boundary}--\r\n'.encode('ascii')


def _policy_expired(response):
    if response.status not in (400, 403):
        return False
    try:
        error = ElementTree.fromstring(response.read(4096))
    except ElementTree.ParseError:
        return False
    return error.findtext('Message', '').strip() == 'Invalid according to Policy: Policy expired.'


def upload_audio(path, get_policy, checkpoint):
    deadline = time.monotonic() + UPLOAD_SECONDS

    def check():
        checkpoint()
        if time.monotonic() >= deadline:
            raise AdapterError('timeout')

    with path.open('rb') as audio:
        size = path.stat().st_size
        for _ in range(2):
            check()
            issued = time.monotonic()
            host, directory, fields, seconds, expires, minimum, maximum, file_limit = _upload_policy(get_policy(check))
            policy_deadline = min(issued + seconds, time.monotonic() + (
                expires - datetime.now(timezone.utc)).total_seconds()) - 2
            check()
            if time.monotonic() >= policy_deadline:
                continue
            key = f'{directory}/{uuid4().hex}.m4a'
            fields.update(key=key, success_action_status='200')
            boundary = uuid4().hex
            length = size + sum(map(len, _multipart(None, fields, boundary, check)))
            if not 0 < size <= file_limit or not minimum <= length <= maximum:
                raise AdapterError('evidence_budget')

            def check_upload():
                check()
                if time.monotonic() >= policy_deadline:
                    raise AdapterError('timeout')

            audio.seek(0)
            check_upload()
            connection = http.client.HTTPSConnection(host, timeout=8)
            try:
                connection.request('POST', '/', body=_multipart(audio, fields, boundary, check_upload), headers={
                    'Content-Type': f'multipart/form-data; boundary={boundary}',
                    'Content-Length': str(length),
                })
                check_upload()
                response = connection.getresponse()
                if response.status in (200, 201, 204):
                    logger.info('Temporary ASR audio uploaded; provider expiry applies')
                    check()
                    return 'oss://' + key
                if _policy_expired(response):
                    continue
                if response.status == 429:
                    raise AdapterError('rate_limit')
                if response.status in (408, 504):
                    raise AdapterError('timeout')
                if response.status >= 500:
                    logger.warning('Temporary ASR upload outcome unknown; provider expiry applies if stored')
                raise AdapterError('unavailable')
            except AdapterError as error:
                if error.kind == 'timeout':
                    logger.warning('Temporary ASR upload outcome unknown; provider expiry applies if stored')
                raise
            except (OSError, http.client.HTTPException) as error:
                logger.warning('Temporary ASR upload outcome unknown; provider expiry applies if stored')
                raise AdapterError('timeout' if isinstance(error, TimeoutError) else 'unavailable') from None
            finally:
                connection.close()
    raise AdapterError('timeout')
