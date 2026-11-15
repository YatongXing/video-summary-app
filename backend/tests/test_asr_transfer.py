"""No-network ASR audio transfer and cleanup tests."""
import os
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from app.qwen_cloud import CloudVideoStore


class AsrTransferTests(unittest.TestCase):
    def setUp(self):
        self.store = CloudVideoStore('cn-hangzhou', 'fixture-bucket', 'fixture-id', 'fixture-secret')
        self.task_id = uuid4()
        self.key = self.store.asset_key(self.task_id, 'video.mov')
        gate = patch.dict(os.environ, {'MAINTENANCE_GATE_REQUIRED': 'false'})
        gate.start()
        self.addCleanup(gate.stop)

    def test_audio_cleanup_requires_matching_locator_and_actual_absence(self):
        from app.asr_transfer import AUDIO_KIND, clean_audio_asset
        from app.cleanup import _physical
        self.store.client = Mock()
        self.store.client.get_bucket_versioning.return_value.version_status = None
        asset = dict(id=uuid4(), asset_kind=AUDIO_KIND, task_id=self.task_id,
                     storage_key=self.store.audio_asset_key(self.task_id), storage_bucket=self.store.bucket,
                     storage_backend=self.store.backend, storage_version=None)
        for wrong in (dict(asset, task_id=uuid4()), dict(asset, storage_key=self.key),
                      dict(asset, storage_bucket='other-bucket'), dict(asset, storage_version='version')):
            with patch.object(CloudVideoStore, 'from_environment', return_value=self.store), self.assertRaises(ValueError):
                _physical(wrong)
        self.store.client.delete_object.assert_not_called()
        with patch('app.asr_transfer.tasks.connect') as connect:
            for status, code in ((403, 'AccessDenied'), (404, 'NoSuchBucket'), (404, 'NoSuchKey')):
                error = self.store.oss.exceptions.ServiceError(
                    status_code=status, code=code, request_id='', message='', ec='', timestamp='', request_target='')
                self.store.client.head_object.side_effect = self.store.oss.exceptions.OperationError(name='HeadObject', error=error)
                if code == 'NoSuchKey':
                    self.assertTrue(clean_audio_asset(asset, self.store))
                    connect.return_value.__enter__.return_value.execute.assert_called_once()
                else:
                    with self.assertLogs('app.asr_transfer', level='WARNING'):
                        self.assertFalse(clean_audio_asset(asset, self.store))
                    connect.assert_not_called()
