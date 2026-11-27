"""Shared contract for the private Bilibili import and processing path."""
import json
import math

FULL = 'FULL_MEDIA'
SUBTITLES = 'PLATFORM_SUBTITLES'
AUDIO = 'AUDIO_ONLY'
FILES = {FULL: 'media.mp4', SUBTITLES: 'subtitle.json', AUDIO: 'media.m4a'}
MAX_TRANSCRIPT_BYTES = 512 * 1024
MAX_CUES = 6000
AUDIO_BYTES = 64 * 1024 * 1024
VISUAL_BYTES = 256 * 1024 * 1024
DISK_HEADROOM = 128 * 1024 * 1024
AUTO_DOWNLOAD_SECONDS = 90
VISUAL_DOWNLOAD_SECONDS = 600
AUTO_RELAY_SECONDS = 100
VISUAL_RELAY_SECONDS = 630
AUTO_IMPORT_SECONDS = 240
VISUAL_IMPORT_SECONDS = 900

LABELS = {
    FULL: {'zh': '依据：完整媒体（结合画面）。', 'en': 'Evidence: full media, including visuals.'},
    SUBTITLES: {'zh': '依据：平台字幕（未分析画面）。', 'en': 'Evidence: platform subtitles. Visuals were not analyzed.'},
    AUDIO: {'zh': '依据：语音转写（未分析画面）。', 'en': 'Evidence: speech transcription. Visuals were not analyzed.'},
}


def language_family(value):
    if not isinstance(value, str):
        return None
    family = value.lower().removeprefix('ai-').split('-', 1)[0]
    return family if family in ('zh', 'en') else None


def encode(document):
    return json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def segments(rows, duration, *, require_coverage=False):
    if not isinstance(rows, list) or not 0 < len(rows) <= MAX_CUES:
        raise ValueError('Invalid cue count')
    normalized = []
    previous, covered, right = -1, 0, 0
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError('Invalid cue')
        start, end, text = row
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= duration or start < previous
                or not isinstance(text, str) or not text.strip() or '\x00' in text):
            raise ValueError('Invalid cue')
        normalized.append([start, end, text.strip()])
        previous = start
        covered += max(0, end - max(start, right))
        right = max(right, end)
    # Allow natural pauses, but reject short opening-only or badly truncated tracks.
    if require_coverage and (normalized[0][0] > duration * .3 or right < duration * .6 or covered < duration * .15):
        raise ValueError('Insufficient subtitle coverage')
    if len(encode(normalized)) > MAX_TRANSCRIPT_BYTES:
        raise ValueError('Transcript exceeds byte budget')
    return normalized


def parse_subtitles(raw, duration):
    if len(raw) > MAX_TRANSCRIPT_BYTES:
        raise ValueError('Subtitle file exceeds byte budget')
    document = json.loads(raw)
    if not isinstance(document, dict) or document.keys() != {'language', 'segments'}:
        raise ValueError('Invalid subtitle document')
    if document['language'] not in ('zh', 'en'):
        raise ValueError('Invalid subtitle language')
    return segments(document['segments'], duration, require_coverage=True)


def fit_visual_subtitles(raw, declared_duration, media_duration):
    """Fit validated cues to the decoded media clock after the two durations are matched."""
    cues = parse_subtitles(raw, declared_duration)
    fitted = [[start, min(end, media_duration), text] for start, end, text in cues if start < media_duration]
    return encode({'language': json.loads(raw)['language'],
                   'segments': segments(fitted, media_duration, require_coverage=True)})
