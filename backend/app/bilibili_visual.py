"""Local contact sheets for one validated Bilibili part."""
from bisect import bisect_right
from hashlib import sha256
import json
from errno import EDQUOT, ENOSPC
import logging
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from app.bilibili_evidence import AUDIO_BYTES, DISK_HEADROOM, VISUAL_BYTES
from app.model_adapter import AdapterError, VisualEvidence
from app.preprocess import PreparedAudio, PreprocessError, _run

INTERVAL = 6
MAX_SAMPLES = 1000
MAX_JPEG_BYTES = 192 * 1024
CELL_WIDTH, CELL_HEIGHT = 960, 540
SHEET_SIZE = 4
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
PREPARE_SECONDS = 300
SUMMARY_SECONDS = 1200
logger = logging.getLogger(__name__)


def sample_times(duration):
    last = max(0, math.ceil(duration) - 1)
    if last == 0:
        return (0,)
    count = min(MAX_SAMPLES, math.ceil(last / INTERVAL) + 1)
    if last > INTERVAL * (MAX_SAMPLES - 1):
        return tuple(round(i * last / (count - 1)) for i in range(count))
    return (*range(0, last, INTERVAL), last)


def _video_sample_times(source, duration, run):
    probe = [os.getenv('FFPROBE_BIN', 'ffprobe'), '-v', 'error', '-protocol_whitelist', 'file,pipe',
             '-format_whitelist', 'mov', '-select_streams', 'v:0']
    metadata = json.loads(run(probe + ['-show_entries', 'format=start_time:stream=start_time,duration',
                              '-of', 'json', str(source)], stdout=subprocess.PIPE).stdout)
    if not metadata.get('streams'):
        raise AdapterError('visual_media')
    origin = float(metadata['format'].get('start_time', 0))
    # In MP4/MOV, packet presentation times describe display order even when
    # B-frames are stored in decode order. Discard edit-list preroll packets.
    rows = run(probe + ['-show_packets', '-show_entries', 'packet=pts_time,flags',
                       '-of', 'csv=p=0', str(source)], stdout=subprocess.PIPE).stdout
    pts = []
    for row in rows.splitlines():
        fields = row.split(b',')
        if len(fields) < 2 or fields[0] in (b'', b'N/A') or b'D' in fields[1]:
            continue
        t = round(float(fields[0]) - origin, 6)
        if 0 <= t < duration:
            pts.append(t)
    if not pts:
        raise AdapterError('visual_media')
    pts.sort()
    first, last = pts[0], pts[-1]
    targets = list(sample_times(last - first + 1))
    targets = [min(last, first + t) for t in targets]
    targets[-1] = last
    return [(target, pts[bisect_right(pts, target) - 1]) for target in targets]


def ensure_space(directory, required=0):
    if shutil.disk_usage(directory).free < required + DISK_HEADROOM:
        raise AdapterError('workspace_space')


def _ffmpeg(*, verbose='error'):
    return [os.getenv('FFMPEG_BIN', 'ffmpeg'), '-nostdin', '-v', verbose,
            '-xerror', '-filter_threads', '1', '-threads', '2',
            '-protocol_whitelist', 'file,pipe']


def _sheet(inputs, run):
    filters = []
    for index, (timestamp, _) in enumerate(inputs):
        minutes, seconds = divmod(int(timestamp), 60)
        label = f'{minutes:02d}\\:{seconds:02d}'
        if timestamp % 1:
            label += f'{timestamp % 1:.6f}'[1:].rstrip('0')
        filters.append(f"[{index}:v]setsar=1,drawtext=fontfile={FONT}:text='{label}':"
                       f'fontsize=48:fontcolor=yellow:borderw=1:bordercolor=black:x=10:y=10[cell{index}]')
    for index in range(len(inputs), SHEET_SIZE):
        filters.append(f'color=c=black:s={CELL_WIDTH}x{CELL_HEIGHT}:d=1[cell{index}]')
    cells = ''.join(f'[cell{index}]' for index in range(SHEET_SIZE))
    filters.append(f'{cells}xstack=inputs=4:layout=0_0|{CELL_WIDTH}_0|0_{CELL_HEIGHT}|{CELL_WIDTH}_{CELL_HEIGHT}[sheet]')
    command = _ffmpeg()
    for _, path in inputs:
        command.extend(('-i', str(path)))
    command += ['-filter_complex_threads', '1', '-filter_complex', ';'.join(filters),
                '-map', '[sheet]', '-frames:v', '1', '-threads', '1', '-f', 'image2pipe']
    for quality in (3, 6, 10, 16, 24, 31):
        jpeg = run(command + ['-q:v', str(quality), '-'], stdout=subprocess.PIPE).stdout
        if 0 < len(jpeg) <= MAX_JPEG_BYTES:
            return jpeg
    raise AdapterError('evidence_budget')


def _prepare_audio(source, directory, duration, run):
    probe = run([os.getenv('FFPROBE_BIN', 'ffprobe'), '-v', 'error',
        '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov',
        '-show_entries', 'format=start_time:stream=codec_type,start_time', '-of', 'json', str(source)],
        stdout=subprocess.PIPE)
    metadata = json.loads(probe.stdout)
    stream = next(s for s in metadata['streams'] if s['codec_type'] == 'audio')
    offset = float(stream.get('start_time', 0)) - float(metadata['format'].get('start_time', 0))
    start = max(0, offset)
    if not start < duration:
        raise AdapterError('input')
    audio = directory / 'visual-audio.m4a'
    encoded = run(_ffmpeg(verbose='info') + ['-format_whitelist', 'mov', '-i', str(source),
        '-map', '0:a:0', '-vn', '-af', f'atrim=start={max(0, -offset)},asetpts=PTS-STARTPTS,'
        'astats=measure_perchannel=none:measure_overall=Peak_level+RMS_level',
        '-t', str(duration - start), '-ac', '1', '-ar', '16000', '-c:a', 'aac', '-b:a', '48k',
        '-movflags', '+faststart', str(audio)], stderr=subprocess.PIPE)
    levels = {kind: float(level) for kind, level in re.findall(
        rb'astats[^\r\n]*? (Peak|RMS) level dB: (-inf|-?[0-9.]+)', encoded.stderr)}
    if levels.get(b'Peak', 0) <= -85 and levels.get(b'RMS', 0) <= -95:
        audio.unlink()
        return None
    if not 0 < audio.stat().st_size <= AUDIO_BYTES:
        raise AdapterError('evidence_budget')
    actual = run([os.getenv('FFPROBE_BIN', 'ffprobe'), '-v', 'error', '-select_streams', 'a:0',
                  '-show_entries', 'stream=duration', '-of', 'csv=p=0', str(audio)], stdout=subprocess.PIPE)
    end = start + float(actual.stdout)
    if not start < end <= duration + .1:
        raise AdapterError('input')
    return PreparedAudio(audio, start, min(duration, end), 0)


def prepare_visual(source: Path, duration, *, needs_audio, checkpoint):
    """Keep only four intermediate frames, and bound the encoded sheets in memory."""
    directory = source.parent
    deadline = time.monotonic() + PREPARE_SECONDS
    peak = source.stat().st_size

    def run(command, **options):
        ensure_space(directory)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AdapterError('timeout')
        try:
            return _run(command, checkpoint=checkpoint, timeout=min(60, remaining), check=True, **options)
        except PreprocessError as error:
            if error.code == 'PREPROCESS_TIMEOUT':
                raise AdapterError('timeout') from None
            raise
        except subprocess.CalledProcessError:
            ensure_space(directory)
            raise AdapterError('visual_media') from None

    times = _video_sample_times(source, duration, run)
    previous = None
    group = []
    frames = []
    distinct = 0
    for index, (timestamp, frame_pts) in enumerate(times):
        last_sample = index == len(times) - 1
        # Decode the final second in reverse to retain the last displayed frame.
        # Some containers round the duration above the end of the video stream.
        offsets = (frame_pts, max(0, frame_pts - 1)) if last_sample and frame_pts else (frame_pts,)
        filters = f'scale={CELL_WIDTH}:{CELL_HEIGHT}:flags=lanczos'
        if last_sample:
            filters = 'reverse,' + filters
        for offset in offsets:
            try:
                jpeg = run(_ffmpeg() + ['-ss', str(offset), '-format_whitelist', 'mov', '-i', str(source),
                    '-map', '0:v:0', '-frames:v', '1', '-vf', filters,
                    '-threads', '1', '-q:v', '2', '-f', 'image2pipe', '-'], stdout=subprocess.PIPE).stdout
            except AdapterError as error:
                if error.kind == 'visual_media' and offset != offsets[-1]:
                    continue
                raise
            if jpeg:
                break
        else:
            raise AdapterError('visual_media')
        digest = sha256(jpeg).digest()
        if digest == previous and index != len(times) - 1:
            continue
        previous = digest
        distinct += 1
        path = directory / f'cell-{len(group)}.jpg'
        try:
            path.write_bytes(jpeg)
        except OSError as error:
            if error.errno in (ENOSPC, EDQUOT):
                raise AdapterError('workspace_space') from None
            raise
        group.append((timestamp, path))
        peak = max(peak, source.stat().st_size + sum(file.stat().st_size for _, file in group))
        if len(group) == SHEET_SIZE:
            stamps = tuple(stamp for stamp, _ in group)
            frames.append(VisualEvidence(stamps[0], _sheet(group, run), sampled_seconds=stamps))
            for _, file in group:
                file.unlink()
            group.clear()
    if group:
        stamps = tuple(stamp for stamp, _ in group)
        frames.append(VisualEvidence(stamps[0], _sheet(group, run), sampled_seconds=stamps))
    for _, file in group:
        file.unlink()
    logger.info('Bilibili contact sheets %s', json.dumps({
        'duration': duration, 'first_sample': times[0][0] if times else None, 'last_sample': times[-1][0] if times else None,
        'samples': len(times), 'distinct': distinct, 'tail_frames': len(group), 'sheets': len(frames),
        'sheet_bytes': sum(len(frame.data) for frame in frames),
    }))
    if not frames:
        raise AdapterError('visual_too_few')
    audio = _prepare_audio(source, directory, duration, run) if needs_audio else None
    peak = max(peak, source.stat().st_size + (audio.path.stat().st_size if audio else 0))
    logger.info('Bilibili visual workspace %s', json.dumps({'worker_peak_file_bytes': peak, 'audio_prepared': bool(audio)}))
    return tuple(frames), audio
