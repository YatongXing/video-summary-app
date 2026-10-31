"""Local audio/visual evidence preparation; never makes model/network requests."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import wave

from app.media import InputError, validate_media

POLICY_VERSION = 'local-evidence-v1'


class PreprocessError(Exception):
    def __init__(self, code='PREPROCESS_FAILED', category='INTERNAL'):
        self.code, self.category = code, category
        super().__init__(code)


@dataclass(frozen=True)
class PreparedFrame:
    path: Path = field(repr=False)
    timestamp_seconds: float
    track_index: int = 0


@dataclass(frozen=True)
class PreparedAudio:
    path: Path = field(repr=False)
    start_seconds: float
    end_seconds: float
    track_index: int = 0


@dataclass(frozen=True)
class PreparedMedia:
    duration_seconds: float
    frames: tuple[PreparedFrame, ...]
    audio: PreparedAudio | None
    audio_tracks: tuple[PreparedAudio, ...]
    limitations: tuple[str, ...]
    policy_version: str = POLICY_VERSION
    directory: Path = field(default=Path('.'), repr=False)


def _checkpoint(checkpoint):
    if checkpoint is None:
        return
    try:
        if checkpoint() is False:
            raise PreprocessError('PREPROCESS_CANCELLED')
    except PreprocessError:
        raise
    except Exception:
        raise PreprocessError('PREPROCESS_CANCELLED') from None


def _run(command, *, checkpoint=None, timeout=3600, check=False,
         capture_output=False, stdout=None, stderr=None, **kwargs):
    """Drain output to private temporary files while polling cancellation."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        output = out if capture_output or stdout == subprocess.PIPE else stdout
        errors = err if capture_output or stderr == subprocess.PIPE else stderr
        _checkpoint(checkpoint)
        process = subprocess.Popen(command, stdout=output, stderr=errors, **kwargs)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                _checkpoint(checkpoint)
                if time.monotonic() >= deadline:
                    raise PreprocessError('PREPROCESS_TIMEOUT')
                time.sleep(.05)
            _checkpoint(checkpoint)
            out.seek(0); err.seek(0)
            result = subprocess.CompletedProcess(command, process.returncode,
                out.read() if output is out else None,
                err.read() if errors is err else None)
            if check and result.returncode:
                raise subprocess.CalledProcessError(result.returncode, command)
            return result
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def _finite(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def prepare_media(source_path, work_dir, checkpoint=None) -> PreparedMedia:
    """Prepare all tracks on one source-relative timeline; own only a fresh child directory.

    Frames are periodic evidence, not proof that every visual event was retained.
    Full audio tracks are preserved without source-video segmentation.
    """
    source = Path(source_path).resolve()
    parent = Path(work_dir).resolve()
    owned = None
    try:
        interval = float(os.getenv('PREPROCESS_FRAME_INTERVAL_SECONDS', '10'))
        width = int(os.getenv('PREPROCESS_FRAME_WIDTH', '768'))
        timeout = float(os.getenv('PREPROCESS_TIMEOUT_SECONDS', '3600'))
        if not math.isfinite(interval) or interval < .1 or not 16 <= width <= 3840 or not math.isfinite(timeout) or not 1 <= timeout <= 10800:
            raise PreprocessError('PREPROCESS_CONFIGURATION')
        _checkpoint(checkpoint)
        runner = lambda command, **kwargs: _run(command, checkpoint=checkpoint, **kwargs)
        info = validate_media(source, runner=runner)
        _checkpoint(checkpoint)
        if checkpoint is None:
            parent.mkdir(parents=True, exist_ok=True)
        elif not parent.is_dir():
            raise PreprocessError('PREPROCESS_CANCELLED')
        owned = Path(tempfile.mkdtemp(prefix='evidence-', dir=parent))
        probe = _run([os.getenv('FFPROBE_BIN', 'ffprobe'), '-v', 'error',
            '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov',
            '-show_entries', 'format=start_time:stream=index,codec_type,start_time,duration',
            '-of', 'json', str(source)], checkpoint=checkpoint,
            capture_output=True, check=True, timeout=min(120, timeout))
        metadata = json.loads(probe.stdout)
        origin = _finite(metadata.get('format', {}).get('start_time'))
        videos = [s for s in metadata.get('streams', []) if s.get('codec_type') == 'video']
        audios = [s for s in metadata.get('streams', []) if s.get('codec_type') == 'audio']
        base = [os.getenv('FFMPEG_BIN', 'ffmpeg'), '-nostdin', '-v', 'info', '-xerror',
            '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'mov', '-threads', '2']
        # select and showinfo preserve actual selected frame PTS, including non-zero origins.
        filters = []
        for index in range(len(videos)):
            filters.append(f"[0:v:{index}]select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval})',scale='min({width},iw)':-2,showinfo@v{index}[v{index}]")
        command = base + ['-copyts', '-i', str(source), '-filter_complex_threads', '1', '-filter_complex', ';'.join(filters)]
        for index in range(len(videos)):
            command += ['-map', f'[v{index}]', '-fps_mode', 'vfr', '-q:v', '3', str(owned / f'frame-{index}-%06d.jpg')]
        extracted = _run(command, checkpoint=checkpoint, capture_output=True, check=True, timeout=timeout)
        timestamps = {i: [] for i in range(len(videos))}
        for line in extracted.stderr.decode('utf-8', errors='replace').splitlines():
            match = re.search(r'showinfo@v(\d+).*?pts_time:([-+\d.eE]+)', line)
            if match:
                index = int(match.group(1))
                stamp = float(match.group(2)) - origin
                if not math.isfinite(stamp) or stamp < -.05 or stamp > info.duration_seconds + .05:
                    raise PreprocessError('PREPROCESS_TIMELINE')
                timestamps[index].append(min(info.duration_seconds, max(0.0, stamp)))
        frames = []
        for index, stamps in timestamps.items():
            paths = sorted(owned.glob(f'frame-{index}-*.jpg'))
            if not paths or len(paths) != len(stamps) or any(a >= b for a, b in zip(stamps, stamps[1:])):
                raise PreprocessError('PREPROCESS_TIMELINE')
            frames.extend(PreparedFrame(path, stamp, index) for path, stamp in zip(paths, stamps))
        frames.sort(key=lambda frame: (frame.timestamp_seconds, frame.track_index))
        audio_tracks = []
        if audios:
            command = base + ['-i', str(source)]
            starts = []
            for index, stream in enumerate(audios):
                offset = _finite(stream.get('start_time'), origin) - origin
                start = max(0.0, offset)
                starts.append(start)
                command += ['-map', f'0:a:{index}', '-vn', '-af', f'asetpts=PTS-STARTPTS,atrim=start={max(0.0, -offset)}:duration={max(0.0, info.duration_seconds-start)},asetpts=PTS-STARTPTS',
                    '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(owned / f'audio-{index}.wav')]
            _run(command, checkpoint=checkpoint, capture_output=True, check=True, timeout=timeout)
            for index, start in enumerate(starts):
                path = owned / f'audio-{index}.wav'
                with wave.open(str(path), 'rb') as audio:
                    duration = audio.getnframes() / audio.getframerate()
                if duration <= 0 or start > info.duration_seconds or start + duration > info.duration_seconds + .1:
                    raise PreprocessError('PREPROCESS_TIMELINE')
                audio_tracks.append(PreparedAudio(path, start, min(info.duration_seconds, start + duration), index))
        _checkpoint(checkpoint)
        limitations = [f'视觉证据按约 {interval:g} 秒间隔采样，不能据此声称覆盖所有视觉事件。',
            '音频按源轨道偏移对齐，源时间线之外的编码填充已去除；尚未执行语音识别或模型质量验证。']
        if not audio_tracks:
            limitations.append('视频无音轨，无可用语音证据。')
        return PreparedMedia(info.duration_seconds, tuple(frames), audio_tracks[0] if audio_tracks else None,
            tuple(audio_tracks), tuple(limitations), directory=owned)
    except PreprocessError:
        if owned is not None: shutil.rmtree(owned)
        raise
    except InputError as error:
        if owned is not None: shutil.rmtree(owned)
        category = 'INTERNAL' if error.status == 503 or error.code == 'MEDIA_TIMEOUT' else 'USER_INPUT'
        raise PreprocessError(error.code, category) from None
    except FileNotFoundError:
        if owned is not None: shutil.rmtree(owned)
        raise PreprocessError('PREPROCESS_UNAVAILABLE') from None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        if owned is not None: shutil.rmtree(owned)
        raise PreprocessError() from None
