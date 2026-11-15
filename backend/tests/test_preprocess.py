"""Independent local FFmpeg evidence and timing checks; no external media."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import wave
import pytest
from app import preprocess

@pytest.fixture
def clip(tmp_path):
    if not shutil.which('ffmpeg'): pytest.skip('Run Docker test target with FFmpeg')
    def make(name='clip.mp4', audio=True, offset=0, duration=3, tracks=1, pts=0, volume=1):
        target=tmp_path/name
        args=['ffmpeg','-nostdin','-y','-v','error','-f','lavfi','-i',f'color=c=red:s=64x48:r=10:d={duration}']
        if audio:
            for index in range(tracks):
                args+=['-itsoffset',str(offset),'-f','lavfi','-i',f'sine=frequency={400+index*100}:duration={duration-offset},volume={volume}']
        args+=['-map','0:v','-c:v','libx264','-threads','1','-pix_fmt','yuv420p']
        if audio:
            for index in range(tracks): args+=['-map',f'{index+1}:a']
            args+=['-c:a','aac']
        if pts: args+=['-output_ts_offset',str(pts)]
        subprocess.run(args+[str(target)],check=True,capture_output=True,timeout=30)
        return target
    return make

def test_frames_audio_full_span_and_common_timeline(clip,tmp_path,monkeypatch):
    monkeypatch.setenv('PREPROCESS_FRAME_INTERVAL_SECONDS','1')
    result=preprocess.prepare_media(clip(duration=3),tmp_path/'work')
    assert 2.9<=result.duration_seconds<=3.2
    times=[f.timestamp_seconds for f in result.frames]
    assert times==sorted(times) and len(times)>=3
    assert times[0]==pytest.approx(0,abs=.11) and times[-1]>=1.9
    assert all(0<=t<=result.duration_seconds for t in times)
    for frame in result.frames: assert frame.path.read_bytes().startswith(b'\xff\xd8')
    assert result.audio is not None
    with wave.open(str(result.audio.path),'rb') as stream:
        assert stream.getnchannels()==1 and stream.getframerate()==16000
        seconds=stream.getnframes()/stream.getframerate()
    assert seconds>=2.9
    assert result.audio.end_seconds-result.audio.start_seconds==pytest.approx(seconds,abs=.02)
    assert result.policy_version and str(tmp_path) not in repr(result)

def test_longer_source_sampling_is_explicit_not_silent_truncation(clip,tmp_path,monkeypatch):
    monkeypatch.setenv('PREPROCESS_FRAME_INTERVAL_SECONDS','10')
    result=preprocess.prepare_media(clip(duration=12),tmp_path/'work')
    assert result.duration_seconds>=11.9
    assert len(result.frames)>=2 and result.frames[-1].timestamp_seconds>=9.9
    with wave.open(str(result.audio.path),'rb') as stream: assert stream.getnframes()/stream.getframerate()>=11.9
    assert result.limitations

def test_silent_source_is_valid_and_discloses_missing_audio(clip,tmp_path):
    result=preprocess.prepare_media(clip(audio=False),tmp_path/'work')
    assert result.frames and result.audio is None and not result.audio_tracks
    assert result.limitations

def test_audio_offset_and_nonzero_source_pts_are_preserved(clip,tmp_path,monkeypatch):
    monkeypatch.setenv('PREPROCESS_FRAME_INTERVAL_SECONDS','1')
    result=preprocess.prepare_media(clip(name='offset.mov',offset=1,pts=5),tmp_path/'work')
    assert result.frames[0].timestamp_seconds==pytest.approx(0,abs=.11)
    assert .85<=result.audio.start_seconds<=1.1
    assert result.audio.end_seconds>result.audio.start_seconds

def test_all_audio_tracks_are_retained(clip,tmp_path):
    result=preprocess.prepare_media(clip(tracks=2),tmp_path/'work')
    assert len(result.audio_tracks)==2
    assert len({a.track_index for a in result.audio_tracks})==2

def test_deepseek_evidence_uses_bounded_frames_and_first_audio_track(clip, tmp_path, monkeypatch):
    monkeypatch.setenv('PREPROCESS_FRAME_INTERVAL_SECONDS', '.1')
    result = preprocess.prepare_media(clip(tracks=2, duration=12, offset=1), tmp_path/'work', max_frames=12)
    times = [frame.timestamp_seconds for frame in result.frames]
    assert len(times) <= 12 and times == sorted(set(times)) and times[-1] >= 10
    assert len(result.audio_tracks) == 1 and result.audio.start_seconds == pytest.approx(1, abs=.1)
    assert result.audio.end_seconds == pytest.approx(12, abs=.1)
    assert result.audio.path.suffix == '.m4a'
    probe = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                     'stream=codec_name,profile,channels,sample_rate,start_time,duration',
                                     '-of', 'json', str(result.audio.path)])
    stream, = json.loads(probe)['streams']
    assert (stream['codec_name'], stream['profile'], stream['channels'], stream['sample_rate']) == ('aac', 'LC', 1, '16000')
    assert float(stream['start_time']) == 0
    assert float(stream['duration']) == pytest.approx(11, abs=.1)
    assert any('first video and audio tracks' in item for item in result.limitations)


@pytest.mark.parametrize('audio,volume,retained', [(False,1,False),(True,0,False),(True,1e-5,False),(True,.01,True)])
def test_deepseek_keeps_quiet_audio_with_evidence(clip,tmp_path,audio,volume,retained):
    result = preprocess.prepare_media(clip(audio=audio,volume=volume,offset=.5),tmp_path/'work',max_frames=12)
    assert bool(result.audio) is retained
    assert len(list(result.directory.glob('*.m4a'))) == int(retained)
    assert result.frames and result.duration_seconds == pytest.approx(3, abs=.1)


def test_deepseek_aac_retains_audio_and_gap_with_overlapping_source_timestamps(clip, tmp_path):
    original = clip(duration=5)
    source = tmp_path / 'overlapping.mp4'
    # Squeeze five packet timestamps together without changing their AAC payloads.
    # Later, introduce a distinct forward gap that the transcode must retain.
    pts = 'if(between(N,46,50),PTS-(N-46)*1023,PTS)+if(gte(N,115),22050,0)'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(original), '-map', '0',
                    '-c', 'copy', '-bsf:a', f"setts=pts='{pts}':dts='{pts}'", str(source)],
                   check=True, capture_output=True, timeout=30)

    result = preprocess.prepare_media(source, tmp_path / 'work', max_frames=12)
    audio, = result.audio_tracks
    output = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                                      '-show_entries', 'packet=dts', '-of', 'json', str(audio.path)])
    stamps = [int(packet['dts']) for packet in json.loads(output)['packets']]
    steps = [later - earlier for earlier, later in zip(stamps, stamps[1:])]
    assert min(steps) > 0
    assert max(steps) / 16000 == pytest.approx(.564, abs=.05)
    assert audio.end_seconds - audio.start_seconds == pytest.approx(5.5, abs=.1)
    decoded = subprocess.check_output(['ffmpeg', '-nostdin', '-v', 'error', '-xerror',
                                       '-i', str(audio.path), '-f', 's16le', '-'])
    assert len(decoded) / (2 * 16000) == pytest.approx(5, abs=.1)


def test_deepseek_two_hour_audio_remains_one_bounded_file(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('Run Docker test target with FFmpeg')
    source = tmp_path / 'two-hours.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error',
                    '-f', 'lavfi', '-i', 'color=c=blue:s=32x32:r=1:d=7200',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=7200',
                    '-c:v', 'libx264', '-threads', '1', '-c:a', 'aac', '-b:a', '32k', str(source)],
                   check=True, capture_output=True, timeout=150)
    prepared = preprocess.prepare_media(source, tmp_path/'work', max_frames=12)
    audio, = prepared.audio_tracks
    assert audio.end_seconds - audio.start_seconds == pytest.approx(7200, abs=.2)
    assert 0 < audio.path.stat().st_size < 60_000_000
    assert list(prepared.directory.glob('*.m4a')) == [audio.path]
    assert len(prepared.frames) <= 12


def test_invalid_and_cancelled_preparation_remove_owned_outputs(clip,tmp_path):
    work=tmp_path/'work'; work.mkdir(); sentinel=work/'keep.txt';sentinel.write_text('keep')
    bad=tmp_path/'bad.mp4';bad.write_text('<html>not media</html>')
    with pytest.raises(preprocess.PreprocessError): preprocess.prepare_media(bad,work)
    with pytest.raises(preprocess.PreprocessError) as error: preprocess.prepare_media(clip(),work,checkpoint=lambda:False)
    assert 'CANCEL' in error.value.code
    assert list(work.iterdir())==[sentinel]

def test_commands_only_allow_local_protocols(clip,tmp_path,monkeypatch):
    original=preprocess.subprocess.Popen;commands=[]
    def capture(args,*pos,**kwargs):
        commands.append(args);return original(args,*pos,**kwargs)
    monkeypatch.setattr(preprocess.subprocess,'Popen',capture)
    preprocess.prepare_media(clip(),tmp_path/'work')
    filtered=[args for args in commands if '-protocol_whitelist' in args]
    assert filtered
    assert all(args[args.index('-protocol_whitelist')+1]=='file,pipe' for args in filtered)


def test_running_child_is_reaped_on_cancel_or_timeout(monkeypatch):
    import sys
    original=preprocess.subprocess.Popen; children=[]
    def recording(*args,**kwargs):
        process=original(*args,**kwargs);children.append(process);return process
    monkeypatch.setattr(preprocess.subprocess,'Popen',recording)
    calls=[0]
    def checkpoint():
        calls[0]+=1
        return calls[0]<4
    with pytest.raises(preprocess.PreprocessError) as error:
        preprocess._run([sys.executable,'-c','import time; time.sleep(30)'],checkpoint=checkpoint)
    assert error.value.code=='PREPROCESS_CANCELLED'
    assert children[-1].poll() is not None
    with pytest.raises(preprocess.PreprocessError) as error:
        preprocess._run([sys.executable,'-c','import time; time.sleep(30)'],timeout=.1)
    assert error.value.code=='PREPROCESS_TIMEOUT'
    assert children[-1].poll() is not None
