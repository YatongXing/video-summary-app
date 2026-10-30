"""Actual-media validation without permitting network demuxer inputs."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import subprocess

MAX_BYTES = 2_000_000_000
MAX_DURATION = 7200

class InputError(Exception):
    def __init__(self, code='INVALID_MEDIA', detail='视频无效、损坏或不符合支持的格式。', status=422):
        self.code, self.detail, self.status = code, detail, status
        super().__init__(code)

@dataclass(frozen=True)
class MediaInfo:
    duration_seconds: float
    has_audio: bool
    container: str

def validate_media(path: Path) -> MediaInfo:
    size=path.stat().st_size
    if size > MAX_BYTES: raise InputError('FILE_TOO_LARGE','单个视频不能超过 2 GB。',413)
    if not size: raise InputError()
    try:
        probe=subprocess.run([os.getenv('FFPROBE_BIN','ffprobe'),'-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov','-show_entries','format=format_name,duration:stream=codec_type,codec_name,duration','-of','json',str(path)],capture_output=True,timeout=max(1,min(120,int(os.getenv('MEDIA_PROBE_TIMEOUT_SECONDS','30')))),check=True)
        data=json.loads(probe.stdout)
        duration=float(data['format']['duration'])
        names=set(data['format']['format_name'].split(','))
        with path.open('rb') as f: head=f.read(4096)
        if 'mov' in names:
            offset=0; brands=None
            while offset+8<=len(head):
                atom_size=int.from_bytes(head[offset:offset+4],'big')
                atom_type=head[offset+4:offset+8]
                if atom_type==b'ftyp':
                    if atom_size<16 or offset+atom_size>len(head): raise InputError()
                    brands=head[offset+8:offset+atom_size]; break
                if atom_size<8: break
                offset+=atom_size
            if brands is not None:
                major=brands[:4]
                if major not in (b'isom',b'iso2',b'iso3',b'iso4',b'iso5',b'iso6',b'avc1',b'mp41',b'mp42',b'M4V ',b'qt  '): raise InputError('UNSUPPORTED_MEDIA','仅支持 MP4 或 MOV 容器。')
                container='mov' if major==b'qt  ' else 'mp4'
            elif head[4:8] in (b'moov',b'mdat',b'wide',b'free'):
                container='mov'
            else: raise InputError()
        else: raise InputError()
        streams=data.get('streams',[])
        videos=[s for s in streams if s.get('codec_type')=='video']
        audios=[s for s in streams if s.get('codec_type')=='audio']
        if not videos or any(s.get('codec_name')!='h264' for s in videos) or any(s.get('codec_name') not in ('aac','mp3') for s in audios):
            raise InputError('UNSUPPORTED_MEDIA','仅支持 H.264 视频及 AAC/MP3 音轨。')
        if not math.isfinite(duration) or duration<=0: raise InputError()
        if duration>MAX_DURATION: raise InputError('VIDEO_TOO_LONG','视频时长不能超过 2 小时。')
        result=subprocess.run([os.getenv('FFMPEG_BIN','ffmpeg'),'-nostdin','-v','error','-xerror','-err_detect','explode','-protocol_whitelist','file,pipe','-format_whitelist','mov','-threads','2','-i',str(path),'-map','0:v','-map','0:a?','-threads','2','-progress','pipe:1','-stats_period','10','-f','null','-'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=max(1,min(10800,int(os.getenv('MEDIA_DECODE_TIMEOUT_SECONDS','3600')))),check=True)
        times=[int(line.split('=',1)[1]) for line in result.stdout.decode('ascii',errors='ignore').splitlines() if line.startswith('out_time_us=') and line.split('=',1)[1].lstrip('-').isdigit()]
        if times and max(times)>MAX_DURATION*1_000_000: raise InputError('VIDEO_TOO_LONG','视频时长不能超过 2 小时。')
        return MediaInfo(duration,bool(audios),container)
    except InputError: raise
    except FileNotFoundError: raise InputError('MEDIA_SERVICE_UNAVAILABLE','媒体校验服务暂不可用。',503) from None
    except subprocess.TimeoutExpired: raise InputError('MEDIA_TIMEOUT','媒体校验超时，请稍后重试。',422) from None
    except (subprocess.CalledProcessError,ValueError,KeyError,TypeError,OSError): raise InputError() from None
