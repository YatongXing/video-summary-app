"""Bilibili URL validation and bounded transfer from the private downloader."""
import asyncio
from errno import EDQUOT, ENOSPC
import math
import os
import re
import shutil
from urllib.parse import parse_qs, urlsplit

import httpx

from app.media import InputError
from app.bilibili_evidence import (
    AUDIO_BYTES, AUTO_IMPORT_SECONDS, AUTO_RELAY_SECONDS, DISK_HEADROOM, FILES, FULL,
    MAX_TRANSCRIPT_BYTES, SUBTITLES, VISUAL_IMPORT_SECONDS, VISUAL_RELAY_SECONDS, VISUAL_BYTES,
    parse_subtitles,
)

IMPORT_SECONDS = AUTO_IMPORT_SECONDS
_ERRORS = {
    'PART_MISMATCH': (422, '所选分 P 不存在或视频身份无法确认。'),
    'RESTRICTED': (422, '此视频无法匿名完整获取，请改用有权处理的本地文件。'),
    'TLS': (502, '连接 Bilibili 时发生 TLS 错误。'),
    'TIMEOUT': (504, 'Bilibili 下载或校验超时。'),
    'EGRESS': (422, '字幕地址超出当前受限出口范围。'),
    'NO_AUDIO': (422, '未找到独立音轨，请尝试勾选“结合画面”。'),
    'NO_MEDIA': (422, '没有可校验的完整音视频。'),
    'TOO_LARGE': (413, 'Bilibili 视频超过导入大小上限。'),
    'NO_SPACE': (503, '当前暂存空间不足，请稍后重试。'),
    'BUSY': (429, '正在处理另一项 Bilibili 导入。'),
    'UNAVAILABLE': (503, 'Bilibili 下载暂不可用。'),
}


def enabled():
    return os.getenv('BILIBILI_IMPORT_ENABLED', 'true') == 'true'


def source_url(value):
    error = InputError('BILIBILI_URL_INVALID', '请输入 HTTPS Bilibili BV 播放页链接和有效分 P。')
    if any(not ch.isprintable() or ch.isspace() for ch in value):
        raise error
    try:
        url = urlsplit(value)
        parts = parse_qs(url.query, keep_blank_values=True, max_num_fields=32).get('p', ['1'])
    except ValueError:
        raise error from None
    video = re.fullmatch(r'/video/(BV[A-Za-z0-9]{10})/?', url.path)
    if (url.scheme != 'https' or url.netloc.lower() not in ('bilibili.com', 'www.bilibili.com')
            or not video or len(parts) != 1 or not re.fullmatch(r'[1-9][0-9]{0,3}', parts[0])):
        raise error
    return video[1], int(parts[0])


def failure(code):
    if code not in _ERRORS:
        code = 'UNAVAILABLE'
    status, message = _ERRORS[code]
    return InputError('BILIBILI_' + code, message, status)


async def download(source, path, *, combine_visual=False):
    bvid, part = source
    socket = os.getenv('BILIBILI_DOWNLOAD_SOCKET', '/run/bilibili-api/download.sock')
    response_started = False
    if combine_visual and shutil.disk_usage(path.parent).free < VISUAL_BYTES + DISK_HEADROOM:
        raise failure('NO_SPACE')
    try:
        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket), trust_env=False,
                                    timeout=httpx.Timeout(VISUAL_RELAY_SECONDS if combine_visual else AUTO_RELAY_SECONDS, connect=3)) as client:
            async with client.stream('GET', f'http://downloader/{bvid}/{part}/{"visual" if combine_visual else "auto"}') as response:
                response_started = True
                if response.status_code != 200:
                    raise failure(response.headers.get('x-import-error', ''))
                if response.headers.get('x-import-source') != f'{bvid}:{part}':
                    raise failure('PART_MISMATCH')
                kind = response.headers.get('x-import-evidence')
                if kind not in FILES or (kind == FULL) != combine_visual:
                    raise failure('NO_MEDIA')
                try:
                    size = int(response.headers['content-length'])
                    seconds = float(response.headers['x-import-duration'])
                    prefix = int(response.headers.get('x-import-subtitle-bytes', '0'))
                except (KeyError, ValueError):
                    raise failure('NO_MEDIA') from None
                limit = MAX_TRANSCRIPT_BYTES if kind == SUBTITLES else VISUAL_BYTES if combine_visual else AUDIO_BYTES
                if not 0 <= prefix <= (MAX_TRANSCRIPT_BYTES if combine_visual else 0):
                    raise failure('NO_MEDIA')
                if size - prefix > limit:
                    raise failure('TOO_LARGE')
                if size <= prefix or not math.isfinite(seconds) or not 0 < seconds <= 7200:
                    raise failure('NO_MEDIA')
                total = 0
                subtitle = bytearray()
                with path.open('xb') as target:
                    async for chunk in response.aiter_raw(chunk_size=65536):
                        total += len(chunk)
                        if total > size:
                            raise failure('NO_MEDIA')
                        leading = max(0, min(len(chunk), prefix - len(subtitle)))
                        subtitle.extend(chunk[:leading])
                        if combine_visual and shutil.disk_usage(path.parent).free < len(chunk) + DISK_HEADROOM:
                            raise failure('NO_SPACE')
                        target.write(chunk[leading:])
                        await asyncio.sleep(0)
                if total != size:
                    raise failure('NO_MEDIA')
                try:
                    if prefix:
                        parse_subtitles(subtitle, seconds)
                except (ValueError, UnicodeError):
                    raise failure('NO_MEDIA') from None
                return seconds, kind, bytes(subtitle) if prefix else None
    except OSError as error:
        if error.errno in (ENOSPC, EDQUOT):
            raise failure('NO_SPACE') from None
        raise
    except httpx.TimeoutException:
        raise failure('TIMEOUT') from None
    except httpx.RemoteProtocolError:
        raise failure('NO_MEDIA' if response_started else 'UNAVAILABLE') from None
    except httpx.HTTPError:
        raise failure('UNAVAILABLE') from None
