from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.errors import error_response
from app.core.logging import bind_log_context, clear_log_context, get_logger
from app.core.observability import counter, histogram

access_logger = get_logger("app.access")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cache-Control", "no-store")
        return response


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-Id") or uuid4().hex
        user_agent = request.headers.get("user-agent")
        bind_log_context(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
            client_ip=request.client.host if request.client else "unknown",
            user_agent=user_agent,
        )
        request.state.request_id = request_id
        started = monotonic()
        try:
            response = await call_next(request)
        except Exception:
            access_logger.exception(
                "Unhandled request failure",
                extra={"extra_json": {"status_code": 500, "duration_ms": int((monotonic() - started) * 1000)}},
            )
            clear_log_context()
            raise

        duration_ms = int((monotonic() - started) * 1000)
        route = request.scope.get("route")
        route_label = getattr(route, "path", request.url.path)
        response.headers.setdefault("X-Request-Id", request_id)
        counter(
            "eduground_http_requests_total",
            labels={"method": request.method, "path": route_label, "status_code": response.status_code},
            description="Total HTTP requests by route and status",
        )
        histogram(
            "eduground_http_request_duration_ms",
            duration_ms,
            labels={"method": request.method, "path": route_label, "status_code": response.status_code},
            description="HTTP request latency in milliseconds",
        )
        access_logger.info(
            "HTTP request completed",
            extra={
                "extra_json": {
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                    "route": route_label,
                    "response_headers": {"content_type": response.headers.get("content-type")},
                }
            },
        )
        clear_log_context()
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            max_allowed_bytes = settings.max_api_request_bytes
            if request.url.path == f"{settings.api_prefix}/storage/local-upload":
                max_allowed_bytes = settings.max_upload_bytes
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    size = int(content_length)
                except ValueError:
                    return error_response(
                        request=request,
                        status_code=400,
                        code="invalid_request",
                        message="Invalid Content-Length header",
                        retryable=False,
                    )
                if size > max_allowed_bytes:
                    return error_response(
                        request=request,
                        status_code=413,
                        code="payload_too_large",
                        message=f"Request body too large. Maximum allowed size is {max_allowed_bytes} bytes.",
                        retryable=False,
                    )
        return await call_next(request)


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = Lock()

    async def dispatch(self, request: Request, call_next) -> Response:
        if settings.clerk_enabled:
            return await call_next(request)
        if request.method == "POST" and request.url.path in {
            f"{settings.api_prefix}/auth/login",
            f"{settings.api_prefix}/auth/signup",
        }:
            client_ip = request.client.host if request.client else "unknown"
            key = (client_ip, request.url.path)
            current_time = monotonic()
            with self._lock:
                bucket = self._buckets[key]
                while bucket and current_time - bucket[0] > settings.auth_rate_limit_window_seconds:
                    bucket.popleft()
                if len(bucket) >= settings.auth_rate_limit_requests:
                    counter(
                        "eduground_auth_failures_total",
                        labels={"reason": "rate_limited", "path": request.url.path},
                        description="Authentication failures by reason",
                    )
                    return error_response(
                        request=request,
                        status_code=429,
                        code="rate_limited",
                        message="Too many authentication attempts. Please wait before trying again.",
                        retryable=True,
                    )
                bucket.append(current_time)
        return await call_next(request)
