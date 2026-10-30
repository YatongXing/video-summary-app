import pytest
from fastapi import HTTPException

from app import main


def test_liveness() -> None:
    assert main.live() == {"status": "alive"}


def test_readiness_when_database_is_available(monkeypatch) -> None:
    monkeypatch.setattr(main, "database_ready", lambda: True)
    monkeypatch.setattr(main, "object_store_ready", lambda: True)
    assert main.ready() == {"status": "ready"}


def test_readiness_when_database_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(main, "database_ready", lambda: False)
    monkeypatch.setattr(main, "object_store_ready", lambda: True)
    with pytest.raises(HTTPException) as exc:
        main.ready()
    assert exc.value.status_code == 503
    assert exc.value.detail == "dependencies unavailable"


def test_readiness_when_object_store_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(main, "database_ready", lambda: True)
    monkeypatch.setattr(main, "object_store_ready", lambda: False)
    with pytest.raises(HTTPException) as exc:
        main.ready()
    assert exc.value.status_code == 503
    assert exc.value.detail == "dependencies unavailable"


def test_health_routes_are_registered() -> None:
    paths = {getattr(route, "path", None) for route in main.app.routes}
    assert "/health/live" in paths
    assert "/health/ready" in paths
