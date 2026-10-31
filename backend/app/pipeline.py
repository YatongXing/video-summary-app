"""Offline application loop; validated output commits before success is visible."""
from app import processing, results
from app.model_adapter import FakeModelAdapter


def run_pipeline(context, adapter=None):
    """One attempt, both languages, no network adapter selected implicitly."""
    adapter = adapter if adapter is not None else FakeModelAdapter()
    try:
        response = processing.prepare_and_analyze(context, adapter)
        if context.cancelled.is_set() or not context.checkpoint():
            raise processing.ProcessingCancelled('Processing cancelled')
        with context._settle_lock:
            if context.cancelled.is_set() or context.settled.is_set():
                raise processing.ProcessingCancelled('Processing cancelled')
            metadata = results.publish(context.id, context.generation, context.worker_id,
                response.localized_candidates,
                synthetic=response.model_version.startswith('offline-fake-'))
            context.settled.set()
            return metadata
    except processing.ProcessingCancelled:
        raise
    except results.OutputError:
        context.fail('INTERNAL', 'OUTPUT_INVALID')
        raise
    except Exception:
        # prepare_and_analyze already settles its own errors. This fallback is
        # idempotent and covers persistence/validation failures after analysis.
        context.fail('INTERNAL', 'RESULT_PROCESSING_FAILED')
        raise
