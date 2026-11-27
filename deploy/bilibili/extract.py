"""Pinned yt-dlp integration. Only allow one complete, identified Bilibili part."""
from errno import EDQUOT, ENOSPC, EFBIG
from hashlib import sha256
import http.client
import ssl
import time
import json
import re
import resource
import shutil
import subprocess
from pathlib import Path
import sys
from urllib.parse import urlsplit

from bilibili_evidence import (
    AUDIO, AUDIO_BYTES, DISK_HEADROOM, FILES, FULL, MAX_TRANSCRIPT_BYTES, SUBTITLES,
    VISUAL_BYTES, encode, language_family, segments,
)
from egress import permitted

from yt_dlp import YoutubeDL
from yt_dlp.extractor.bilibili import BiliBiliIE
from yt_dlp.utils import YoutubeDLError
from yt_dlp.networking.common import Request
from yt_dlp.networking.exceptions import HTTPError, RequestError
from yt_dlp.version import __version__

VERSION = '2026.10.26'
RANGE_BYTES = 16 * 1024 * 1024
VISUAL_VIDEO = 'bv[protocol=https][ext=mp4][vcodec^=avc]'
VISUAL_AUDIO = 'ba[protocol=https][ext=m4a][acodec^=mp4a]'
COMBINED = 'b[protocol=https][ext=mp4][vcodec^=avc][acodec^=mp4a]'


class Rejected(Exception):
    pass


class Diagnostics:
    reason = 'UNAVAILABLE'

    def stage(self, name, **fields):
        print('BILIBILI_VISUAL ' + json.dumps({'stage': name, **fields}), file=sys.stderr, flush=True)

    def debug(self, _message):
        pass

    def warning(self, message):
        self.error(message)

    def error(self, message):
        text = message.lower()
        if any(term in text for term in ('unexpected_eof', 'certificate verify', 'sslerror', '[ssl')):
            self.reason = 'TLS'
        elif any(term in text for term in ('http error 401', 'http error 403', 'http error 412',
                                            'http error 429', 'login', 'geo-restricted', 'supporter-only',
                                            'captcha', 'video info: 352')):
            self.reason = 'RESTRICTED'
        elif 'timed out' in text:
            self.reason = 'TIMEOUT'
        elif 'requested format is not available' in text:
            self.reason = 'NO_MEDIA'
        elif any(term in text for term in ('no space left', 'disk quota exceeded')):
            self.reason = 'NO_SPACE'
        elif any(term in text for term in ('max-filesize', 'file too large')):
            self.reason = 'TOO_LARGE'


def part_details(document, bvid, part):
    if document.get('code') != 0:
        raise Rejected('RESTRICTED' if document.get('code') in (-401, -403, -412, -352) else 'PART_MISMATCH')
    data = document['data']
    if data.get('bvid') != bvid:
        raise Rejected('PART_MISMATCH')
    rights = data.get('rights', {})
    if any(rights.get(flag) for flag in ('is_stein_gate', 'pay', 'ugc_pay')) or data.get('is_upower_exclusive') or data.get('redirect_url'):
        raise Rejected('RESTRICTED')
    pages = [page for page in data['pages'] if page['page'] == part]
    if len(pages) != 1 or not 0 < pages[0]['duration'] <= 7200 or not pages[0]['cid'] > 0:
        raise Rejected('PART_MISMATCH')
    return pages[0]['cid'], pages[0]['duration']


def read_subtitle_json(ydl, url, limit=MAX_TRANSCRIPT_BYTES):
    target = urlsplit(url)
    if (target.scheme != 'https' or not permitted(target.hostname or '')
            or target.port not in (None, 443) or target.username or target.password):
        raise Rejected('EGRESS')
    with ydl.urlopen(url) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        return None, len(raw)
    try:
        return json.loads(raw), len(raw)
    except (ValueError, RecursionError):
        return None, len(raw)


def subtitle_candidates(manifest):
    if not isinstance(manifest, dict):
        return []
    if manifest.get('code') in (-101, -401, -403):
        return []
    if manifest.get('code') != 0:
        raise Rejected('RESTRICTED' if manifest.get('code') in (-412, -429, -352) else 'UNAVAILABLE')
    data = manifest.get('data') or {}
    if data.get('need_login_subtitle'):
        return []
    listing = data.get('subtitle') or {}
    tracks = [(t, language_family(t.get('lan'))) for t in listing.get('subtitles') or []
              if isinstance(t, dict) and isinstance(t.get('subtitle_url'), str) and t['subtitle_url']]
    tracks = [(t, lang) for t, lang in tracks if lang]
    original_language = listing.get('original_lan') or listing.get('lan')
    declared = language_family(original_language)
    if original_language and not declared:
        return []
    if not declared and len({lang for _, lang in tracks}) != 1:
        return []
    return sorted(((track, lang) for track, lang in tracks if not declared or lang == declared),
                  key=lambda item: item[0]['lan'].lower().startswith('ai-'))


def platform_subtitles(ydl, bvid, cid, duration):
    try:
        manifest, _ = read_subtitle_json(ydl, f'https://api.bilibili.com/x/player/wbi/v2?bvid={bvid}&cid={cid}')
        remaining = MAX_TRANSCRIPT_BYTES
        attempted = set()
        for track, language in subtitle_candidates(manifest):
            if len(attempted) == 2 or remaining <= 0:
                break
            url = track['subtitle_url']
            if url.startswith('//'):
                url = 'https:' + url
            if url in attempted:
                continue
            attempted.add(url)
            body, size = read_subtitle_json(ydl, url, remaining)
            remaining -= size
            try:
                rows = [[cue['from'], cue['to'], cue['content']] for cue in body['body']]
                text = segments(rows, duration, require_coverage=True)
                document = encode({'language': language, 'segments': text})
            except (KeyError, TypeError, ValueError):
                continue
            if len(document) <= MAX_TRANSCRIPT_BYTES:
                return document
    except HTTPError as error:
        if error.status not in (401, 403):
            raise
    return None


def download_track(ydl, track, target, budget):
    """Read one selected track, in ranges small enough for the restricted tunnel."""
    url = urlsplit(track['url'])
    if (url.scheme != 'https' or not permitted(url.hostname or '') or url.port not in (None, 443)
            or url.username or url.password or track.get('protocol') != 'https'):
        raise Rejected('EGRESS')
    headers = dict(track.get('http_headers') or {})
    headers['Accept-Encoding'] = 'identity'
    offset = 0
    expected = None
    validator = None
    with target.open('xb') as output:
        while expected is None or offset < expected:
            end = offset + RANGE_BYTES - 1 if expected is None else min(expected - 1, offset + RANGE_BYTES - 1)
            headers['Range'] = f'bytes={offset}-{end}'
            if validator:
                headers['If-Range'] = validator
            with ydl.urlopen(Request(track['url'], headers=headers)) as response:
                if response.headers.get('Content-Encoding', 'identity') != 'identity':
                    raise Rejected('NO_MEDIA')
                if response.status == 206:
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                    if match is None:
                        raise Rejected('NO_MEDIA')
                    start, last, length = map(int, match.groups())
                    if start != offset or last != min(end, length - 1) or expected not in (None, length):
                        raise Rejected('NO_MEDIA')
                    if offset == 0:
                        etag = response.headers.get('ETag', '')
                        validator = etag if etag and not etag.startswith('W/') else response.headers.get('Last-Modified')
                    count = last - start + 1
                elif response.status == 200 and offset == 0:
                    length = count = int(response.headers.get('Content-Length', 0))
                else:
                    raise Rejected('NO_MEDIA')
                if length > budget:
                    raise Rejected('TOO_LARGE')
                if count <= 0 or not 0 < length <= budget:
                    raise Rejected('NO_MEDIA')
                expected = length
                remaining = count
                while remaining:
                    chunk = response.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise Rejected('NO_MEDIA')
                    if shutil.disk_usage(target.parent).free < len(chunk) + DISK_HEADROOM:
                        raise Rejected('NO_SPACE')
                    output.write(chunk)
                    remaining -= len(chunk)
                    offset += len(chunk)
                if response.read(1):
                    raise Rejected('NO_MEDIA')
    return offset


def download_visual(ydl, info, directory, logger, subtitle, duration):
    # Subtitle-backed jobs do not need the audio track when DASH is available.
    selection = f'{VISUAL_VIDEO}/{COMBINED}' if subtitle else f'{VISUAL_VIDEO}+{VISUAL_AUDIO}/{COMBINED}/{VISUAL_VIDEO}'
    ydl.format_selector = ydl.build_format_selector(selection)
    selected = ydl.process_ie_result(info, download=False)
    tracks = selected.get('requested_formats') or [selected]
    if not 1 <= len(tracks) <= 2:
        raise Rejected('NO_MEDIA')
    target = directory / FILES[FULL]
    video = target if len(tracks) == 1 else directory / 'video.mp4'
    logger.stage('video_start', height=tracks[0].get('height'), codec='avc')
    video_bytes = download_track(ydl, tracks[0], video, VISUAL_BYTES)
    logger.stage('video_done', video_bytes=video_bytes)
    audio_bytes = 0
    if len(tracks) == 2:
        audio = directory / 'audio.m4a'
        logger.stage('audio_start')
        audio_bytes = download_track(ydl, tracks[1], audio, min(AUDIO_BYTES, VISUAL_BYTES - video_bytes))
        logger.stage('audio_done', audio_bytes=audio_bytes)
        if shutil.disk_usage(directory).free < video_bytes + audio_bytes + DISK_HEADROOM:
            raise Rejected('NO_SPACE')
        # Only the private child is subject to this OS output-file bound. Full decoding
        # and exact duration are checked by the API before an object is published.
        resource.setrlimit(resource.RLIMIT_FSIZE, (VISUAL_BYTES, VISUAL_BYTES))
        status = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-xerror',
            '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov', '-i', str(video),
            '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov', '-i', str(audio),
            '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if status.returncode:
            raise Rejected('TOO_LARGE' if target.exists() and target.stat().st_size >= VISUAL_BYTES else
                           'NO_SPACE' if shutil.disk_usage(directory).free < DISK_HEADROOM else 'NO_MEDIA')
    if subtitle:
        (directory / FILES[SUBTITLES]).write_bytes(subtitle)
    size = target.stat().st_size
    if not 0 < size <= VISUAL_BYTES:
        raise Rejected('TOO_LARGE' if size else 'NO_MEDIA')
    peak = sum(file.stat().st_size for file in directory.iterdir() if file.is_file())
    logger.stage('media_ready', duration=duration, video_bytes=video_bytes, audio_bytes=audio_bytes,
                 staged_bytes=size, downloader_peak_file_bytes=peak, subtitle=bool(subtitle))
    return {'duration': duration, 'evidence': FULL}


def open_admission_metadata(ydl, url, logger):
    # Each extraction runs in a dedicated child process. Scope the connection
    # hook to its first metadata request, including any redirects it follows.
    from yt_dlp.networking._urllib import HTTPHandler
    original_factory = HTTPHandler._make_conn_class
    connected = False
    failure = None

    class AdmissionConnection(http.client.HTTPSConnection):
        def _tunnel(self):
            self.admission_stage = 'proxy_connect'
            super()._tunnel()
            self.admission_stage = 'site_tls'

        def connect(self):
            nonlocal connected, failure
            self.admission_stage = 'proxy_tcp'
            try:
                super().connect()
            except (OSError, http.client.HTTPException) as error:
                reason = getattr(error, 'reason', None)
                if not isinstance(reason, str) or not re.fullmatch('[A-Z0-9_]{1,80}', reason):
                    reason = type(error).__name__
                if self.admission_stage == 'proxy_connect':
                    status = re.search(r'Tunnel connection failed: (\d{3})\b', str(error))
                    if status:
                        reason = 'CONNECT_' + status[1]
                category = ('TLS' if isinstance(error, ssl.SSLError) or self.admission_stage == 'site_tls'
                            else 'TIMEOUT' if isinstance(error, TimeoutError) else 'UNAVAILABLE')
                failure = (self.admission_stage, reason, category)
                self.close()
                raise
            connected = True

    def factory(base, request):
        return original_factory(AdmissionConnection if base is http.client.HTTPSConnection else base, request)

    HTTPHandler._make_conn_class = staticmethod(factory)
    try:
        for attempt in range(1, 4):
            failure = None
            try:
                return ydl.urlopen(url)
            except (RequestError, OSError, http.client.HTTPException):
                if connected or failure is None:
                    raise
                stage, reason, category = failure
                logger.stage('admission_connect_failed', attempt=attempt, phase=stage,
                             reason=reason, category=category, final=attempt == 3)
                if attempt == 3:
                    raise Rejected(category) from None
                time.sleep(1)
    finally:
        HTTPHandler._make_conn_class = staticmethod(original_factory)


def extract(bvid, part, directory, logger, *, combine_visual=False):
    if __version__ != VERSION:
        raise Rejected('UNAVAILABLE')
    url = f'https://www.bilibili.com/video/{bvid}/?p={part}'
    options = {
        'logger': logger, 'proxy': 'http://127.0.0.1:8080', 'cachedir': False,
        'socket_timeout': 10, 'retries': 0, 'fragment_retries': 0,
        'file_access_retries': 0, 'extractor_retries': 0, 'noplaylist': True,
        'geo_bypass': False, 'max_filesize': AUDIO_BYTES,
        'format': 'ba[protocol=https][ext=m4a][vcodec=none][acodec^=mp4a]',
        'outtmpl': str(directory / 'media.%(ext)s'), 'overwrites': False,
    }
    with YoutubeDL(options, auto_init=False) as ydl:
        if combine_visual:
            logger.stage('metadata_start', part=part)
        with open_admission_metadata(ydl, f'https://api.bilibili.com/x/web-interface/view?bvid={bvid}', logger) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise Rejected('UNAVAILABLE')
        cid, duration = part_details(json.loads(raw), bvid, part)
        if combine_visual:
            logger.stage('metadata_done', part=part, cid_fingerprint=sha256(str(cid).encode()).hexdigest()[:12],
                         duration=duration)

        class VerifiedPart(BiliBiliIE):
            verified = False

            def _download_playinfo(self, video, selected_cid, headers=None, query=None, fatal=True):
                if video != bvid or selected_cid != cid:
                    raise Rejected('PART_MISMATCH')
                # Require the selected CID response. Never fall back to webpage playinfo.
                # The preview option is intentionally omitted.
                if combine_visual:
                    logger.stage('playinfo_start')
                result = super()._download_playinfo(video, selected_cid, headers, query=None, fatal=True)
                if (not result or result.get('is_preview')
                        or any('试看' in name for name in result.get('accept_description', []))):
                    raise Rejected('RESTRICTED')
                if combine_visual:
                    logger.stage('playinfo_done')
                self.verified = True
                return result

            def extract_subtitles(self, *_args, **_kwargs):
                return {}

            def _get_chapters(self, *_args):
                return None

        extractor = VerifiedPart(ydl)
        info = extractor.extract(url)
        expected = {f'{bvid}_p{part}'}
        if part == 1:
            expected.add(bvid)
        if (not extractor.verified or info.get('_type', 'video') != 'video'
                or info.get('id') not in expected
                or not isinstance(info.get('duration'), (int, float))
                or not abs(info['duration'] - duration) <= 2):
            raise Rejected('PART_MISMATCH')
        subtitle = platform_subtitles(ydl, bvid, cid, duration)
        if combine_visual:
            logger.stage('subtitles_done', available=bool(subtitle))
            return download_visual(ydl, info, directory, logger, subtitle, duration)
        if subtitle is not None:
            (directory / FILES[SUBTITLES]).write_bytes(subtitle)
            return {'duration': duration, 'evidence': SUBTITLES}
        if not any(f.get('vcodec') == 'none' and f.get('ext') == 'm4a'
                   and (f.get('acodec') or '').startswith('mp4a')
                   and urlsplit(f.get('url') or '').scheme == 'https'
                   for f in info.get('formats') or []):
            raise Rejected('NO_AUDIO')
        ydl.process_ie_result(info, download=True, extra_info={'extractor': 'bilibili',
                              'extractor_key': 'BiliBili', 'webpage_url': url, 'original_url': url})
    media = directory / FILES[AUDIO]
    if not media.is_file() or media.is_symlink() or media.stat().st_size <= 0:
        raise Rejected(logger.reason if logger.reason != 'UNAVAILABLE' else 'NO_MEDIA')
    if media.stat().st_size > AUDIO_BYTES:
        raise Rejected('TOO_LARGE')
    return {'duration': duration, 'evidence': AUDIO}


if __name__ == '__main__':
    diagnostics = Diagnostics()
    try:
        result = extract(sys.argv[1], int(sys.argv[2]), Path.cwd(), diagnostics, combine_visual=sys.argv[3] == 'visual')
    except Rejected as error:
        result = {'error': error.args[0]}
    except (YoutubeDLError, RequestError, OSError, ValueError, KeyError, TypeError) as error:
        if isinstance(error, OSError) and error.errno in (ENOSPC, EDQUOT, EFBIG):
            diagnostics.reason = 'TOO_LARGE' if error.errno == EFBIG else 'NO_SPACE'
        else:
            diagnostics.error(str(error))
        result = {'error': diagnostics.reason}
    if sys.argv[3] == 'visual' and 'error' in result:
        diagnostics.stage('failed', category=result['error'])
    print(json.dumps(result))
