"""Provider-neutral evidence contract. No provider SDK, credentials or network I/O."""
from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import math
import re
import time
from typing import BinaryIO, Callable, Protocol

@dataclass(frozen=True)
class VisualEvidence:
    timestamp_seconds: float
    data: bytes = field(repr=False)
    media_type: str = 'image/jpeg'
    track_index: int = 0

@dataclass(frozen=True)
class AudioEvidence:
    start_seconds: float
    end_seconds: float
    open_stream: Callable[[], BinaryIO] = field(repr=False)
    track_index: int = 0
    media_type: str = 'audio/wav'

@dataclass(frozen=True)
class ModelRequest:
    duration_seconds: float
    frames: tuple[VisualEvidence, ...]
    audio_tracks: tuple[AudioEvidence, ...] = ()
    limitations: tuple[str, ...] = ()
    prompt_version: str = 'summary-v1'
    language: str = 'zh'
    timeout_seconds: float = 120

    @property
    def audio(self):
        return self.audio_tracks[0] if self.audio_tracks else None

@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    estimated_cost: Decimal | None = None
    currency: str | None = None

@dataclass(frozen=True)
class Observation:
    modality: str
    timestamp_seconds: float
    description: str
    track_index: int = 0

@dataclass(frozen=True)
class ModelResponse:
    candidate: dict
    observations: tuple[Observation, ...]
    request_id: str
    usage: TokenUsage
    model_version: str
    adapter_version: str
    localized_candidates: dict | None = None

class AdapterError(Exception):
    CATEGORIES = {'timeout':'EXTERNAL_TIMEOUT', 'rate_limit':'EXTERNAL_RATE_LIMIT',
                  'unavailable':'EXTERNAL_UNAVAILABLE', 'input':'USER_INPUT',
                  'invalid_response':'INTERNAL', 'internal':'INTERNAL', 'cancelled':'INTERNAL'}
    def __init__(self, kind='internal', request_id=None, usage=None):
        self.kind = kind if kind in self.CATEGORIES else 'internal'
        self.request_id = request_id
        self.usage = usage or TokenUsage()
        self.failure_category = self.CATEGORIES[self.kind]
        super().__init__(self.kind)

class ModelAdapter(Protocol):
    model_version: str
    adapter_version: str
    def analyze(self, request: ModelRequest, checkpoint: Callable[[], bool] | None = None) -> ModelResponse: ...

def _time(value, duration):
    return isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value) and 0 <= value <= duration

def validate_usage(usage):
    if not isinstance(usage,TokenUsage):raise AdapterError('invalid_response')
    for count in (usage.input_tokens,usage.output_tokens,usage.cached_tokens):
        if count is not None and (type(count) is not int or not 0<=count<=9223372036854775807):raise AdapterError('invalid_response')
    cost=usage.estimated_cost
    if cost is not None and (not isinstance(cost,Decimal) or not cost.is_finite() or cost<0):raise AdapterError('invalid_response')
    if usage.currency is not None and (not isinstance(usage.currency,str) or not re.fullmatch('[A-Z]{3}',usage.currency)):raise AdapterError('invalid_response')
    if cost is not None and usage.currency is None:raise AdapterError('invalid_response')
    return usage

def validate_response(response: ModelResponse, duration: float):
    if not isinstance(duration,(int,float)) or isinstance(duration,bool) or not math.isfinite(duration) or duration<=0:raise AdapterError('invalid_response')
    if not isinstance(response,ModelResponse) or not isinstance(response.candidate,dict):raise AdapterError('invalid_response')
    for identifier in (response.request_id,response.model_version,response.adapter_version):
        if not isinstance(identifier,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',identifier):raise AdapterError('invalid_response')
    validate_usage(response.usage)
    if not isinstance(response.observations,(tuple,list)):raise AdapterError('invalid_response')
    for observation in response.observations:
        if not isinstance(observation,Observation) or observation.modality not in ('audio','visual') or not _time(observation.timestamp_seconds,duration):raise AdapterError('invalid_response')
        if not isinstance(observation.description,str) or not 0<len(observation.description)<=4096:raise AdapterError('invalid_response')
        if observation.track_index is not None and (type(observation.track_index) is not int or observation.track_index<0):raise AdapterError('invalid_response')
    events=response.candidate.get('key_events',[])
    if not isinstance(events,list):raise AdapterError('invalid_response')
    previous=-1
    for event in events:
        if not isinstance(event,dict) or not _time(event.get('timestamp_seconds'),duration) or event['timestamp_seconds']<previous:raise AdapterError('invalid_response')
        previous=event['timestamp_seconds']
    return response

class FakeModelAdapter:
    """Explicit offline test double; synthetic content says nothing about model quality."""
    model_version='offline-fake-v1'
    adapter_version='evidence-adapter-v1'
    def __init__(self,error_kind=None): self.error_kind=error_kind
    def analyze(self,request,checkpoint=None):
        if not isinstance(request,ModelRequest) or not _time(request.duration_seconds,7200) or request.duration_seconds<=0 or not request.frames or request.language not in ('zh','en'):
            raise AdapterError('input')
        if not math.isfinite(request.timeout_seconds) or request.timeout_seconds<=0: raise AdapterError('input')
        hasher=hashlib.sha256(); observations=[]; previous=-1
        deadline=time.monotonic()+request.timeout_seconds
        def check():
            if time.monotonic()>deadline:raise AdapterError('timeout')
            if checkpoint is not None and checkpoint() is False: raise AdapterError('cancelled')
        for frame in request.frames:
            check()
            if not _time(frame.timestamp_seconds,request.duration_seconds) or frame.timestamp_seconds<previous or not frame.data: raise AdapterError('input')
            previous=frame.timestamp_seconds;hasher.update(frame.data)
        seen=set()
        for frame in request.frames:
            if frame.track_index not in seen:
                observations.append(Observation('visual',frame.timestamp_seconds,'OFFLINE TEST: visual evidence supplied',frame.track_index));seen.add(frame.track_index)
        for track in request.audio_tracks:
            check()
            if not _time(track.start_seconds,request.duration_seconds) or not _time(track.end_seconds,request.duration_seconds) or track.end_seconds<=track.start_seconds: raise AdapterError('input')
            total=0
            with track.open_stream() as stream:
                while True:
                    check();chunk=stream.read(1024*1024)
                    if not chunk:break
                    hasher.update(chunk);total+=len(chunk)
            if not total:raise AdapterError('input')
            observations.append(Observation('audio',track.start_seconds,'OFFLINE TEST: audio evidence supplied',track.track_index))
        request_id='fake-'+hasher.hexdigest()[:32]
        if self.error_kind:raise AdapterError(self.error_kind,request_id=request_id)
        stamp=request.frames[0].timestamp_seconds
        # A single offline call supplies both languages. These are plumbing
        # fixtures, never a translation or semantic quality claim.
        localized={}
        for language in ('zh','en'):
            chinese=language=='zh'
            title='OFFLINE TEST · 离线合成事件' if chinese else 'OFFLINE TEST · synthetic event'
            description='仅验证证据传递，不代表视频内容理解结果。' if chinese else 'Evidence plumbing only; this is not an interpretation of the video.'
            overview='OFFLINE TEST · 离线替身测试结果。' if chinese else 'OFFLINE TEST · synthetic adapter output.'
            limitations=[
                '离线替身未分析真实语义；关键事件少于三个是测试样例限制。' if chinese else 'The offline double does not analyze semantics; this fixture has fewer than three events.',
                '画面按间隔抽样，可能遗漏事件。' if chinese else 'Interval frame sampling may miss events.',
                ('音频已提供，但未执行真实语音理解。' if request.audio_tracks else '没有可用音轨，仅提供画面证据。') if chinese else
                ('Audio was supplied without real speech understanding.' if request.audio_tracks else 'No audio track is available; evidence is visual only.')]
            localized[language]={'language':language,'summary':overview+' '+description,
                'overview':overview,'points':[
                    '已传递抽样画面。' if chinese else 'Sampled visual evidence was supplied.',
                    ('已传递完整音轨。' if request.audio_tracks else '输入没有音轨。') if chinese else
                    ('Complete audio tracks were supplied.' if request.audio_tracks else 'The input has no audio track.'),
                    '未调用外部模型；需后续评测语义质量。' if chinese else 'No external model was called; semantic quality still needs evaluation.'],
                'chapters':[{'timestamp_seconds':stamp,'title':title}],
                'key_events':[{'timestamp_seconds':stamp,'title':title,'description':description}],
                'limitations':limitations}
        candidate=localized[request.language]
        return validate_response(ModelResponse(candidate,tuple(observations),request_id,TokenUsage(),self.model_version,self.adapter_version,localized),request.duration_seconds)
