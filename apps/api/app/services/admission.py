"""Shared, expiring generation leases with fail-closed admission."""
from contextlib import contextmanager
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4

from fastapi import Depends, HTTPException, Request
from redis.exceptions import RedisError

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.observability import counter, histogram
from app.integrations.connections import redis_client

ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
for i,key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
  if redis.call('ZCARD', key) >= tonumber(ARGV[i+2]) then return 0 end
end
for _,key in ipairs(KEYS) do
  redis.call('ZADD', key, now + tonumber(ARGV[2]), ARGV[1])
  redis.call('PEXPIRE', key, tonumber(ARGV[2]) * 2)
end
return 1
"""
RENEW = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
for _,key in ipairs(KEYS) do
  local deadline = redis.call('ZSCORE', key, ARGV[1])
  if not deadline or tonumber(deadline) <= now then return 0 end
end
for _,key in ipairs(KEYS) do
  redis.call('ZADD', key, now + tonumber(ARGV[2]), ARGV[1])
  redis.call('PEXPIRE', key, tonumber(ARGV[2]) * 2)
end
return 1
"""
RELEASE = "for _,key in ipairs(KEYS) do redis.call('ZREM', key, ARGV[1]) end return 1"


@contextmanager
def generation_lease(user_id: str, organization_id: str, session_id: str | None = None, *, client=None):
    if not settings.generation_admission_enabled:
        yield
        return
    client = client if client is not None else redis_client()
    # Hash tag allows these atomic scripts to operate on Redis Cluster too.
    keys = ["eduground:{generation}:global", f"eduground:{{generation}}:user:{user_id}",
            f"eduground:{{generation}}:org:{organization_id}"]
    limits = [settings.generation_max_concurrent, settings.generation_max_per_user,
              settings.generation_max_per_org]
    if session_id:
        keys.append(f"eduground:{{generation}}:session:{session_id}")
        limits.append(1)
    token = uuid4().hex
    ttl_ms = settings.generation_lease_seconds * 1000
    started = monotonic()
    deadline = started + settings.generation_admission_wait_seconds
    while True:
        try:
            accepted = client.eval(ACQUIRE, len(keys), *keys, token, ttl_ms, *limits)
        except RedisError as exc:
            counter("eduground_admission_total", labels={"outcome": "unavailable"})
            raise HTTPException(503, "Generation capacity control unavailable", headers={"Retry-After": "5"}) from exc
        if accepted or monotonic() >= deadline:
            break
        sleep(min(0.1, max(0.0, deadline - monotonic())))
    histogram("eduground_generation_admission_wait_ms", (monotonic() - started) * 1000,
              labels={"outcome": "accepted" if accepted else "rejected"})
    if not accepted:
        counter("eduground_admission_total", labels={"outcome": "rejected"})
        raise HTTPException(429, "Generation capacity is busy; retry shortly", headers={"Retry-After": "5"})
    stopped = Event()
    lost = Event()
    def renew():
        while not stopped.wait(settings.generation_lease_seconds / 3):
            try:
                if not client.eval(RENEW, len(keys), *keys, token, ttl_ms):
                    lost.set()
                    return
            except RedisError:
                lost.set()
                return
    heartbeat = Thread(target=renew, daemon=True, name="generation-lease")
    heartbeat.start()
    counter("eduground_admission_total", labels={"outcome": "accepted"})
    try:
        yield
        if lost.is_set():
            counter("eduground_admission_total", labels={"outcome": "lease_lost"})
    finally:
        stopped.set()
        heartbeat.join(timeout=settings.redis_socket_timeout_seconds + 1)
        try:
            client.eval(RELEASE, len(keys), *keys, token)
        except RedisError:
            # Expiry releases capacity after process/Redis failure.
            counter("eduground_admission_total", labels={"outcome": "release_failed"})


def generation_slot(request: Request, user=Depends(get_current_user)):
    if not settings.generation_admission_enabled:
        yield
        return
    with generation_lease(str(user.id), str(user.institution_id), request.path_params.get("session_id")):
        yield
