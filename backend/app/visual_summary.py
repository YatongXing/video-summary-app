"""Plan complete visual batches against the adapter's serialized request budgets."""
from dataclasses import replace
from hashlib import sha256
import json
import logging
import time

from app.model_adapter import AdapterError, TokenUsage, validate_response

logger = logging.getLogger(__name__)
VISUAL_CALL_MAX_SECONDS = 300
SYNTHESIS_RESERVE_SECONDS = 150
NO_SPEECH = {
    'zh': '没有可用的语音文字材料，摘要依据采样画面。',
    'en': 'No usable speech text was available. The summary uses sampled visuals.',
}


def plan_batches(request, adapter):
    limit = getattr(adapter, 'visual_batch_bytes', adapter.max_summary_bytes)
    frames = request.frames

    def batch(start, stop):
        left = frames[start].timestamp_seconds if start else 0
        right = frames[stop].timestamp_seconds if stop < len(frames) else request.duration_seconds
        return replace(request, frames=frames[start:stop], transcript=tuple(
            segment for segment in request.transcript if segment.start_seconds < right and segment.end_seconds > left
        ), limitations=(*request.limitations,
             f'This batch covers {left:g}–{right:g} seconds on the original timeline.'))

    batches = []
    start = 0
    while start < len(frames):
        low, high = start + 1, min(len(frames), start + adapter.max_summary_images)
        best = None
        while low <= high:
            stop = (low + high) // 2
            candidate = batch(start, stop)
            size = len(adapter.summary_payload(candidate))
            if getattr(adapter, 'supports_visual_facts', False) and len(candidate.frames) < len(frames):
                size = max(size, len(adapter.summary_payload(replace(candidate, visual_facts_only=True))))
            if size <= limit:
                best = candidate
                low = stop + 1
            else:
                high = stop - 1
        if best is None:
            raise AdapterError('evidence_budget')
        batches.append(best)
        start += len(best.frames)
    return batches


def _usage(uses, *, known_only=False):
    def total(key):
        values = [getattr(usage, key) for usage in uses]
        known = [value for value in values if value is not None]
        return sum(known) if known and (known_only or len(known) == len(values)) else None
    return TokenUsage(*(total(key) for key in ('input_tokens', 'output_tokens', 'cached_tokens')))


def _token_counts(usage):
    return {name: getattr(usage, name) for name in ('input_tokens', 'output_tokens', 'cached_tokens')}


def _compact(batch, response):
    return {
        'first_sample': batch.frames[0].sampled_seconds[0],
        'last_sample': batch.frames[-1].sampled_seconds[-1],
        **{language: {'summary': item['summary'][:2000],
                      'chapters': [[c['timestamp_seconds'], c['title'][:128]] for c in item['chapters'][:12]]}
           for language, item in response.localized_candidates.items()},
    }


def summarize_visual(request, adapter, invoke, checkpoint, *, deadline):
    """Publish only after every batch and the optional synthesis have succeeded."""
    batches = plan_batches(request, adapter)
    if not batches:
        raise AdapterError('visual_too_few')
    usages = []
    summaries = []
    facts_mode = len(batches) > 1 and getattr(adapter, 'supports_visual_facts', False)
    inflight = None

    def run(item, number):
        nonlocal inflight
        checkpoint()
        start = time.monotonic()
        budget = min(deadline - start, item.timeout_seconds, VISUAL_CALL_MAX_SECONDS)
        if getattr(adapter, 'visual_batch_bytes', None) and len(batches) > 1:
            remaining_calls = len(batches) + 2 - number
            budget = min(budget, (deadline - start) / remaining_calls)
        if item.visual_facts_only:
            budget = min(budget,
                         (deadline - start - SYNTHESIS_RESERVE_SECONDS) / (len(batches) - number + 1))
        if budget <= 0:
            raise AdapterError('timeout')
        inflight = number
        response = invoke(item, start + budget)
        usages.append(response.usage)
        validate_response(response, request.duration_seconds)
        if item.visual_facts_only != (response.visual_facts is not None):
            raise AdapterError('invalid_response')
        inflight = None
        logger.info('Bilibili visual summary request %s', json.dumps({
            'model': response.model_version, 'call': number, 'images': len(item.frames),
            'phase': 'facts' if item.visual_facts_only else 'synthesis' if item.batch_summaries else 'summary',
            'request_bytes': len(adapter.summary_payload(item)), 'ms': round((time.monotonic() - start) * 1000),
            'budget_ms': round(budget * 1000), 'usage': _token_counts(response.usage),
            'request_fingerprint': sha256(response.request_id.encode()).hexdigest()[:12],
        }))
        return response

    try:
        for number, item in enumerate(batches, 1):
            response = run(replace(item, visual_facts_only=facts_mode), number)
            if facts_mode:
                summaries.append({'first_sample': item.frames[0].sampled_seconds[0],
                                  'last_sample': item.frames[-1].sampled_seconds[-1],
                                  'facts': response.visual_facts})
            elif len(batches) > 1:
                summaries.append(_compact(item, response))
        if summaries:
            last_picture = request.frames[-1].sampled_seconds[-1]
            speech_tail = tuple(cue for cue in request.transcript if cue.end_seconds > last_picture) if facts_mode else ()
            final = replace(request, frames=(), transcript=speech_tail, batch_summaries=tuple(summaries))
            response = run(final, len(batches) + 1)
    except AdapterError as error:
        known = _usage([*usages, error.usage], known_only=True)
        logger.warning('Bilibili visual summary interrupted %s', json.dumps({
            'model': adapter.model_version, 'kind': error.kind, 'inflight_call': inflight,
            'request_fingerprint': sha256(error.request_id.encode()).hexdigest()[:12] if error.request_id else None,
            'known_usage': _token_counts(known),
            'inflight_usage_unknown': inflight is not None and
                                      (error.usage.input_tokens is None or error.usage.output_tokens is None),
        }))
        raise AdapterError(error.kind, request_id=error.request_id, usage=known) from None
    if not request.transcript:
        for language, output in response.localized_candidates.items():
            output['summary'] += '\n\n' + NO_SPEECH[language]
    return replace(response, usage=_usage(usages))
