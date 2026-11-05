from fastapi import HTTPException
import pytest
from redis.exceptions import ConnectionError

from app.core.config import settings
from app.services.admission import generation_lease, ACQUIRE, RELEASE


class Client:
    def __init__(self, accept=True):
        self.accept = accept
        self.calls = []

    def eval(self, script, count, *args):
        self.calls.append((script, count, args))
        return int(self.accept) if script == ACQUIRE else 1


def test_rejected_generation_has_retry_hint_and_no_release(monkeypatch):
    monkeypatch.setattr(settings, "generation_admission_enabled", True)
    client = Client(False)
    with pytest.raises(HTTPException) as exc:
        with generation_lease("user", "org", "session", client=client):
            pytest.fail("rejected work executed")
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "5"
    assert len(client.calls) == 1


def test_exception_releases_exact_lease(monkeypatch):
    monkeypatch.setattr(settings, "generation_admission_enabled", True)
    client = Client()
    with pytest.raises(RuntimeError):
        with generation_lease("user", "org", "session", client=client):
            raise RuntimeError("provider failed")
    acquire, release = client.calls
    assert release[0] == RELEASE
    assert acquire[1] == release[1] == 4
    assert acquire[2][:5] == release[2]


def test_redis_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "generation_admission_enabled", True)
    class Broken:
        def eval(self, *args):
            raise ConnectionError("unavailable")
    with pytest.raises(HTTPException) as exc:
        with generation_lease("user", "org", client=Broken()):
            pytest.fail("must not dispatch")
    assert exc.value.status_code == 503


def test_bounded_wait_accepts_a_released_slot(monkeypatch):
    monkeypatch.setattr(settings, "generation_admission_enabled", True)
    monkeypatch.setattr(settings, "generation_admission_wait_seconds", 0.3)
    client = Client(False)
    calls = 0
    original = client.eval
    def admit_after_first(script, count, *args):
        nonlocal calls
        if script == ACQUIRE:
            calls += 1
            client.accept = calls > 1
        return original(script, count, *args)
    client.eval = admit_after_first
    with generation_lease("user", "org", client=client):
        assert calls == 2
