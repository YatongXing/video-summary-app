import threading

from app.worker import run


def test_worker_stops_when_signalled() -> None:
    stop_event = threading.Event()
    thread = threading.Thread(target=run, args=(stop_event, 0.01))
    thread.start()
    stop_event.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
