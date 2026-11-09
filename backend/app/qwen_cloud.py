"""Temporary, private OSS copies for full-video Qianwen requests."""
from datetime import timedelta
import hashlib
import os
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

from app import maintenance
from app.model_adapter import AdapterError


PREFIX = 'vsa-qwen/'
URL_TTL = timedelta(minutes=15)
ENV_NAMES = ('QWEN_OSS_REGION', 'QWEN_OSS_BUCKET', 'QWEN_OSS_ACCESS_KEY_ID', 'QWEN_OSS_ACCESS_KEY_SECRET')


class CloudVideoStore:
    def __init__(self, region, bucket, key_id, key_secret):
        import alibabacloud_oss_v2 as oss

        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)+', region) or region.startswith('oss-') or not re.fullmatch(
            r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket
        ):
            raise ValueError('Invalid OSS region or bucket')
        endpoint = f'oss-{region}.aliyuncs.com'
        self.bucket = bucket
        self.host = f'{bucket}.{endpoint}'
        self.backend = 'S3:' + hashlib.sha256(f'QWEN_OSS:{self.host}'.encode()).hexdigest()
        self.oss = oss
        self.client = oss.Client(oss.config.Config(
            region=region, endpoint=f'https://{endpoint}', signature_version='v4',
            credentials_provider=oss.credentials.StaticCredentialsProvider(key_id, key_secret),
            retry_max_attempts=1, connect_timeout=5, readwrite_timeout=10, enabled_redirect=False,
        ))

    @classmethod
    def from_environment(cls):
        values = [os.getenv(name, '').strip() for name in ENV_NAMES]
        if not any(values):
            return None
        if not all(values):
            raise ValueError('All four Qwen OSS settings are required')
        return cls(*values)

    def _unversioned(self):
        maintenance.ensure_open()
        state = self.client.get_bucket_versioning(self.oss.GetBucketVersioningRequest(bucket=self.bucket))
        if state.version_status:
            raise ValueError('OSS bucket must never have had versioning enabled')

    def check_bucket(self):
        self._unversioned()
        maintenance.ensure_open()
        acl = self.client.get_bucket_acl(self.oss.GetBucketAclRequest(bucket=self.bucket))
        if acl.acl != 'private':
            raise ValueError('OSS bucket must be private')
        maintenance.ensure_open()
        public_access = self.client.get_bucket_public_access_block(
            self.oss.GetBucketPublicAccessBlockRequest(bucket=self.bucket)
        ).public_access_block_configuration
        if public_access is None or public_access.block_public_access is not True:
            raise ValueError('OSS bucket must block public access')
        maintenance.ensure_open()
        lifecycle = self.client.get_bucket_lifecycle(self.oss.GetBucketLifecycleRequest(bucket=self.bucket))
        config = lifecycle.lifecycle_configuration
        rules = config.rules or [] if config else []
        if not any(
            rule.status == 'Enabled' and PREFIX.startswith(rule.prefix or '')
            and not rule.tags and rule.filter is None
            and rule.expiration and rule.expiration.days == 1
            for rule in rules
        ):
            raise ValueError('OSS bucket requires a one-day expiration rule for temporary videos')

    def asset_key(self, task_id, filename):
        extension = 'mov' if (filename or '').lower().endswith('.mov') else 'mp4'
        return f'{PREFIX}{task_id}/{uuid4().hex}.{extension}'

    def upload(self, path, key, checkpoint):
        last_check = time.monotonic()
        deadline = last_check + 1800

        def progress(*_):
            nonlocal last_check
            now = time.monotonic()
            if now > deadline:
                raise AdapterError('cloud_transfer')
            if now - last_check >= 1:
                checkpoint()
                last_check = now

        checkpoint()
        self.client.put_object_from_file(self.oss.PutObjectRequest(
            bucket=self.bucket, key=key, content_length=path.stat().st_size, acl='private',
            content_type='video/quicktime' if key.endswith('.mov') else 'video/mp4',
            progress_fn=progress,
        ), str(path))
        checkpoint()
        url = self.client.presign(self.oss.GetObjectRequest(bucket=self.bucket, key=key), expires=URL_TTL).url
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != self.host or parsed.path != '/' + key or not parsed.query:
            raise AdapterError('cloud_transfer')
        return url

    def remove(self, asset):
        key = asset['storage_key']
        prefix = f"{PREFIX}{asset['task_id']}/"
        if (asset['storage_backend'] != self.backend or asset['storage_bucket'] != self.bucket
                or asset['storage_version'] or not key.startswith(prefix)
                or not re.fullmatch(r'[0-9a-f]{32}\.(mp4|mov)', key[len(prefix):])):
            raise ValueError('OSS cleanup locator does not match its task and bucket')
        self._unversioned()
        maintenance.ensure_open()
        self.client.delete_object(self.oss.DeleteObjectRequest(bucket=self.bucket, key=key))
        maintenance.ensure_open()
        try:
            self.client.head_object(self.oss.HeadObjectRequest(bucket=self.bucket, key=key))
        except self.oss.exceptions.OperationError as error:
            service_error = error.unwrap()
            if (isinstance(service_error, self.oss.exceptions.ServiceError)
                    and service_error.status_code == 404 and service_error.code != 'NoSuchBucket'):
                self._unversioned()  # A missing bucket is not confirmation that a key was deleted.
                return
            raise
        raise ValueError('OSS temporary object remains after deletion')
