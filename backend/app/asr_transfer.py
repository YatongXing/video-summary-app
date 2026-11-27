"""Cleanup for legacy app-owned OSS audio transfers."""
import logging

from app import maintenance, tasks
from app.qwen_cloud import AUDIO_KIND, CloudVideoStore


logger = logging.getLogger(__name__)


def remove_audio_asset(asset, cloud=None, checkpoint=None):
    if asset['asset_kind'] != AUDIO_KIND:
        raise ValueError('Unexpected ASR asset kind')
    store = cloud if cloud is not None else CloudVideoStore.from_environment()
    if store is None:
        from app.cleanup import CleanupFailure
        raise CleanupFailure('CONFIG', 'ASR_OSS_UNCONFIGURED')
    store.remove(asset, checkpoint=checkpoint)


def clean_audio_asset(asset, cloud=None):
    """Mark only a verified absence; keep the locator available for recovery."""
    if not maintenance.ready():
        return False
    try:
        from app.cleanup import _asr_cleanup_checkpoint
        remove_audio_asset(asset, cloud, checkpoint=_asr_cleanup_checkpoint)
        with tasks.connect() as connection:
            maintenance.ensure_open()
            connection.execute(
                """UPDATE media_assets SET cleanup_state='DELETED',deleted_at=now()
                   WHERE id=%s AND asset_kind=%s AND cleanup_state<>'DELETED'""",
                (asset['id'], AUDIO_KIND),
            )
        return True
    except Exception:
        logger.warning('Temporary ASR audio cleanup pending for asset %s', asset['id'])
        return False
