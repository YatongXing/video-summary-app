"""Independent queue worker with lease fencing and an injectable processor.

An explicitly selected offline processor completes the application loop using
synthetic outputs. Disabled mode only performs recovery, without task claims.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time
from uuid import uuid4

from app.database import database_ready
from app.storage import object_store_ready
from app import queue as task_queue
from app import tasks, maintenance

logger = logging.getLogger(__name__)



def _progress_path():
    import re,socket
    from pathlib import Path
    name=os.getenv('HOSTNAME') or socket.gethostname()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',name):raise ValueError('Worker identity unavailable')
    root=Path(os.environ['DELETION_JOURNAL_DIR'])/'runtime'
    if not root.is_dir() or root.is_symlink():raise ValueError('Worker control unavailable')
    return name,root/('worker-'+name+'.json')

_last_progress=0.0
def _progress():
    global _last_progress
    if not maintenance.required() or time.monotonic()-_last_progress<5:return
    from app.deletion_control import _write,identity_ready
    if not identity_ready():raise ValueError('Worker control unavailable')
    name,path=_progress_path();_write(path,{'instance':name,'last_progress':time.time()});_last_progress=time.monotonic()

def _progress_ready():
    if not maintenance.required():return True
    try:
        import json
        name,path=_progress_path()
        if path.is_symlink():return False
        state=json.loads(path.read_text());stamp=state['last_progress']
        return state['instance']==name and isinstance(stamp,(float,int)) and 0<=time.time()-stamp<=60
    except Exception:return False

class WorkerContext:
    """A single claimed attempt; all writes must use its current lease token.

    A processor must cooperatively observe ``cancelled`` during long work and
    explicitly call ``complete`` after output validation. Returning is not proof
    of a successful video result. No provider SDK or model call is present here.
    """

    def __init__(self, task: dict, worker_id: str):
        self.task = task
        self.id = task['id']
        self.generation = task['generation']
        self.worker_id = worker_id
        self.cancelled = threading.Event()
        self.settled = threading.Event()
        self._settle_lock = threading.Lock()

    def checkpoint(self) -> bool:
        if self.cancelled.is_set() or self.settled.is_set():
            return False
        try:
            valid = task_queue.heartbeat(self.id, self.generation, self.worker_id)
        except Exception:
            self.cancelled.set()
            raise
        if not valid:
            self.cancelled.set()
        return valid

    def fenced_write(self, callback):
        if self.cancelled.is_set() or self.settled.is_set():
            raise RuntimeError('Worker attempt is no longer writable')
        try:
            return task_queue.fenced_write(self.id, self.generation, self.worker_id, callback)
        except Exception:
            self.cancelled.set()
            raise

    def complete(self):
        with self._settle_lock:
            if self.cancelled.is_set() or self.settled.is_set():
                raise RuntimeError('Worker attempt is no longer writable')
            result = tasks.succeed_task(self.id, self.generation, owner=self.worker_id)
            self.settled.set()
            return result

    def fail(self, category='INTERNAL', code='PROCESSOR_FAILED'):
        with self._settle_lock:
            if self.cancelled.is_set() or self.settled.is_set():
                return None
            result = tasks.fail_task(self.id, self.generation, category, code, owner=self.worker_id)
            self.settled.set()
            return result

    def _stop_owned(self, code):
        """Signal first, then attempt an idempotent, lease-checked failure."""
        self.cancelled.set()
        with self._settle_lock:
            if self.settled.is_set():
                return
            try:
                tasks.fail_task(self.id, self.generation, 'INTERNAL', code, owner=self.worker_id)
                self.settled.set()
            except Exception:
                # No renewal after shutdown. Recovery owns expired attempts.
                logger.warning('Worker task settlement deferred to lease recovery')


def _positive_seconds(value, name):
    value = float(value)
    if value <= 0 or not value < float('inf'):
        raise ValueError(f'{name} must be finite and positive')
    return value


def run(stop_event: threading.Event, interval_seconds: float = 1.0,
        processor=None, worker_id: str | None = None,
        heartbeat_seconds: float | None = None) -> None:
    task_queue.settings()
    interval_seconds = _positive_seconds(interval_seconds, 'Worker interval')
    heartbeat_seconds = _positive_seconds(
        heartbeat_seconds if heartbeat_seconds is not None else os.getenv('WORKER_HEARTBEAT_SECONDS', '30'),
        'Worker heartbeat')
    lease_seconds = _positive_seconds(os.getenv('WORKER_LEASE_SECONDS', '120'), 'Worker lease')
    if heartbeat_seconds >= lease_seconds:
        raise ValueError('Worker heartbeat must be shorter than its lease')
    shutdown_seconds = _positive_seconds(os.getenv('WORKER_SHUTDOWN_SECONDS', '5'), 'Worker shutdown')
    worker_id = worker_id or f'worker-{uuid4()}'
    if not worker_id or len(worker_id) > 128:
        raise ValueError('Invalid worker identifier')
    logger.info('Worker started; processor configured=%s', processor is not None)
    next_workspace_cleanup = 0.0
    while not stop_event.is_set():
        try:
            _progress()
            maintenance.ensure_open()
            if os.environ.get('DATABASE_URL'):
                task_queue.recover_expired()
                if os.environ.get('PROCESSING_WORK_DIR') and time.monotonic() >= next_workspace_cleanup:
                    from app.processing import recover_workspaces
                    recover_workspaces()
                    next_workspace_cleanup = time.monotonic() + 60
            if processor is None:
                stop_event.wait(interval_seconds)
                continue
            task = task_queue.claim_next(worker_id)
        except Exception:
            logger.warning('Worker queue unavailable; retrying after poll interval')
            stop_event.wait(interval_seconds)
            continue
        if task is None:
            stop_event.wait(interval_seconds)
            continue
        context = WorkerContext(task, worker_id)
        finished = threading.Event()
        failed = threading.Event()

        def execute():
            try:
                from app.task_locks import producer,LockBusy
                while not context.cancelled.is_set():
                    try:
                        with producer(context.id):processor(context)
                        break
                    except LockBusy:
                        if not context.checkpoint():break
                        context.cancelled.wait(.05)
            except BaseException:
                failed.set()
            finally:
                finished.set()

        thread = threading.Thread(target=execute, name='video-task-processor', daemon=True)
        thread.start()
        next_heartbeat = time.monotonic() + heartbeat_seconds
        while not finished.wait(min(0.1, heartbeat_seconds)):
            try:_progress()
            except Exception:context.cancelled.set()
            if stop_event.is_set():
                # A blocked processor/DB call cannot keep the worker process alive.
                context.cancelled.set()
                finalizer = threading.Thread(target=context._stop_owned,
                    args=('WORKER_STOPPED',), daemon=True)
                deadline = time.monotonic() + shutdown_seconds
                finalizer.start()
                finalizer.join(max(0, deadline - time.monotonic()))
                thread.join(max(0, deadline - time.monotonic()))
                logger.info('Worker stopped; active attempt fenced or awaiting expiry')
                return
            if not context.cancelled.is_set() and not context.settled.is_set() and time.monotonic() >= next_heartbeat:
                try:
                    context.checkpoint()
                except Exception:
                    logger.warning('Worker heartbeat unavailable; attempt cancelled locally')
                next_heartbeat = time.monotonic() + heartbeat_seconds
        # Do not claim another task while a processor thread remains alive.
        thread.join()
        if not context.cancelled.is_set() and not context.settled.is_set():
            try:
                context.fail('INTERNAL', 'PROCESSOR_FAILED' if failed.is_set() else 'PROCESSOR_INCOMPLETE')
            except Exception:
                context.cancelled.set()
                logger.warning('Worker task failure deferred to lease recovery')
    logger.info('Worker stopped')


def main() -> int:
    parser = argparse.ArgumentParser(description='Video Summary App queue worker')
    parser.add_argument('--healthcheck', action='store_true')
    args = parser.parse_args()
    if args.healthcheck:
        from app.deletion_control import identity_ready
        return 0 if database_ready() and object_store_ready() and identity_ready() and _progress_ready() else 1
    if not os.environ.get('DATABASE_URL') or not os.environ.get('S3_ENDPOINT_URL'):
        logger.error('DATABASE_URL and S3_ENDPOINT_URL are required')
        return 1
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    stop_event = threading.Event()
    def stop(_signum, _frame):
        stop_event.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    cleanup_thread = None
    try:
        from app.deletion_control import replay_journal, prune_journal
        if maintenance.ready():
            try:replay_journal()
            except maintenance.MaintenanceClosed:pass
        if os.getenv('CLEANUP_ENABLED', 'false').lower() == 'true':
            from app.cleanup import run_once
            interval = _positive_seconds(os.getenv('CLEANUP_POLL_SECONDS', '5'), 'Cleanup poll')
            def maintain():
                last_failure_signal = None
                while not stop_event.is_set():
                    try:
                        maintenance.ensure_open()
                        run_once()
                        prune_journal()
                    except Exception:
                        observed = time.monotonic()
                        if last_failure_signal is None or observed - last_failure_signal >= 300:
                            logger.warning('Cleanup remains pending; retrying after poll interval')
                            last_failure_signal = observed
                    stop_event.wait(interval)
            cleanup_thread = threading.Thread(target=maintain, name='task-cleanup', daemon=True)
            cleanup_thread.start()
        mode = os.getenv('WORKER_PROCESSOR_MODE', 'disabled')
        if mode not in ('disabled', 'offline'):
            raise ValueError('Unsupported processor mode')
        if os.getenv('MODEL_CALLS_ENABLED', 'false').lower() != 'false':
            raise ValueError('External model calls are not enabled in this work package')
        processor = None
        if mode == 'offline':
            from app.pipeline import run_pipeline
            processor = run_pipeline
        run(stop_event, interval_seconds=os.getenv('WORKER_POLL_SECONDS', '1'), processor=processor)
    except (ValueError, RuntimeError):
        logger.error('Worker configuration or deletion control is unavailable')
        return 2
    finally:
        stop_event.set()
        if cleanup_thread is not None:
            cleanup_thread.join(timeout=5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
