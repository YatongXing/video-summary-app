"""Actual local media fixtures; never downloads or invokes an external model."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from app import media

@pytest.fixture
def make_media(tmp_path):
    ffmpeg=os.getenv('FFMPEG_BIN','ffmpeg')
    if not shutil.which(ffmpeg): pytest.skip('ffmpeg is required; run Docker test target')
    def create(name='sample.mp4', video='libx264', audio='aac', silent=False):
        path=tmp_path/name
        args=[ffmpeg,'-nostdin','-y','-v','error','-f','lavfi','-i','color=c=blue:s=32x32:r=10:d=1']
        if not silent: args+=['-f','lavfi','-i','sine=frequency=400:duration=1']
        args+=['-c:v',video,'-threads','1','-pix_fmt','yuv420p']
        if not silent: args+=['-c:a',audio,'-shortest']
        if name.endswith(('.mov','.mp4')): args+=['-movflags','+faststart']
        subprocess.run(args+[str(path)],check=True,timeout=30,capture_output=True)
        return path
    return create

@pytest.mark.parametrize('name',['sample.mp4','sample.mov'])
def test_actual_h264_aac_supported_containers(make_media,name):
    info=media.validate_media(make_media(name))
    assert info.has_audio is True
    assert .9 <= info.duration_seconds <= 1.2
    assert info.container in ('mp4','mov')

def test_silent_video_and_actual_content_instead_of_extension(make_media):
    path=make_media(silent=True); renamed=path.with_suffix('.untrusted');path.rename(renamed)
    assert media.validate_media(renamed).has_audio is False

@pytest.mark.parametrize('video,audio',[('mpeg4','aac'),('libx264','pcm_s16le')])
def test_unsupported_actual_codecs_rejected(make_media,video,audio):
    with pytest.raises(media.InputError) as error: media.validate_media(make_media('unsupported.mov',video,audio))
    assert error.value.code=='UNSUPPORTED_MEDIA'

def test_webm_does_not_expand_video_codec_whitelist(make_media):
    with pytest.raises(media.InputError): media.validate_media(make_media('sample.webm','libvpx-vp9','libopus'))

def test_truncated_video_and_html_cannot_pass_validation(make_media,tmp_path):
    path=make_media(); raw=path.read_bytes(); path.write_bytes(raw[:len(raw)//2])
    with pytest.raises(media.InputError): media.validate_media(path)
    html=tmp_path/'player.mp4';html.write_text('<html>not media</html>')
    with pytest.raises(media.InputError):media.validate_media(html)

def test_exact_byte_and_duration_boundaries(make_media,monkeypatch):
    path=make_media(silent=True); size=path.stat().st_size
    monkeypatch.setattr(media,'MAX_BYTES',size)
    monkeypatch.setattr(media,'MAX_DURATION',1)
    assert media.validate_media(path).duration_seconds==1
    monkeypatch.setattr(media,'MAX_BYTES',size-1)
    with pytest.raises(media.InputError) as error:media.validate_media(path)
    assert error.value.code=='FILE_TOO_LARGE'
    monkeypatch.setattr(media,'MAX_BYTES',size)
    monkeypatch.setattr(media,'MAX_DURATION',.5)
    with pytest.raises(media.InputError) as error:media.validate_media(path)
    assert error.value.code=='VIDEO_TOO_LONG'

def test_probe_and_full_decode_forbid_network_protocols(make_media,monkeypatch):
    original=media.subprocess.run; commands=[]
    def recording(args,**kwargs):
        commands.append(args)
        return original(args,**kwargs)
    monkeypatch.setattr(media.subprocess,'run',recording)
    media.validate_media(make_media(silent=True))
    validation=[args for args in commands if '-protocol_whitelist' in args]
    assert len(validation)==2
    for args in validation:assert args[args.index('-protocol_whitelist')+1]=='file,pipe'
    assert '-xerror' in validation[1] and '-f' in validation[1] and 'null' in validation[1]
