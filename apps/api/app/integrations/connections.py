"""Process-owned, bounded connection pools; never cache request/user state."""
from contextlib import contextmanager
from io import BytesIO
from threading import Lock
from urllib.error import HTTPError, URLError

import httpx
from redis import Redis

from app.core.config import settings

_lock = Lock()
_http: httpx.Client | None = None
_redis: Redis | None = None


def http_client() -> httpx.Client:
    global _http
    with _lock:
        if _http is None:
            _http = httpx.Client(
                limits=httpx.Limits(max_connections=settings.provider_max_connections,
                                    max_keepalive_connections=settings.provider_max_connections),
                timeout=httpx.Timeout(60, connect=5, pool=settings.provider_pool_timeout_seconds),
            )
        return _http


def redis_client() -> Redis:
    global _redis
    with _lock:
        if _redis is None:
            _redis = Redis.from_url(
                settings.redis_url, decode_responses=True, max_connections=32,
                socket_connect_timeout=settings.redis_socket_timeout_seconds,
                socket_timeout=settings.redis_socket_timeout_seconds,
            )
        return _redis


@contextmanager
def pooled_urlopen(req, timeout=60):
    """Preserve the adapters' urllib error contract while pooling transports."""
    try:
        response = http_client().request(
            req.get_method(), req.full_url, content=req.data,
            headers=dict(req.header_items()),
            timeout=httpx.Timeout(timeout, connect=min(5, timeout),
                                  pool=settings.provider_pool_timeout_seconds),
        )
    except httpx.HTTPError as exc:
        raise URLError(str(exc)) from exc
    if response.status_code >= 400:
        raise HTTPError(req.full_url, response.status_code, response.reason_phrase,
                        response.headers, BytesIO(response.content))
    with BytesIO(response.content) as body:
        yield body


def close_connections() -> None:
    global _http, _redis
    with _lock:
        if _http is not None:
            _http.close()
            _http = None
        if _redis is not None:
            _redis.close()
            _redis = None
