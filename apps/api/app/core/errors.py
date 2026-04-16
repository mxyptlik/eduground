from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger("app.errors")


@dataclass(slots=True)
class ApiServiceError(RuntimeError):
    code: str
    message: str
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    retryable: bool = False
    provider: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DependencyUnavailableError(ApiServiceError):
    status_code: int = status.HTTP_503_SERVICE_UNAVAILABLE
    retryable: bool = True


@dataclass(slots=True)
class ProviderRequestError(ApiServiceError):
    status_code: int = status.HTTP_502_BAD_GATEWAY
    retryable: bool = True


def _request_id_from_request(request: Request | None) -> str | None:
    if request is None:
        return None
    state_request_id = getattr(request.state, "request_id", None)
    if isinstance(state_request_id, str) and state_request_id.strip():
        return state_request_id
    header_request_id = request.headers.get("X-Request-Id")
    if header_request_id and header_request_id.strip():
        return header_request_id.strip()
    return None


def _status_code_to_code(status_code: int) -> str:
    mapping = {
        400: "invalid_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        413: "payload_too_large",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_error",
        501: "not_implemented",
        502: "upstream_error",
        503: "service_unavailable",
    }
    return mapping.get(status_code, f"http_{status_code}")


def build_error_payload(
    *,
    request: Request | None,
    code: str,
    message: str,
    retryable: bool,
    provider: str | None = None,
    details: dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "detail": message,
        "retryable": retryable,
        "request_id": _request_id_from_request(request),
    }
    if provider:
        payload["provider"] = provider
    if details is not None:
        payload["details"] = details
    return payload


def error_response(
    *,
    request: Request | None,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    provider: str | None = None,
    details: dict[str, Any] | list[Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=build_error_payload(
            request=request,
            code=code,
            message=message,
            retryable=retryable,
            provider=provider,
            details=details,
        ),
    )


def _coerce_http_exception_details(exc: HTTPException) -> tuple[str, str, bool, str | None, dict[str, Any] | list[Any] | None]:
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or _status_code_to_code(exc.status_code))
        message = str(detail.get("message") or detail.get("detail") or "Request failed")
        retryable = bool(detail.get("retryable", exc.status_code >= 500))
        provider = str(detail["provider"]) if detail.get("provider") else None
        details = detail.get("details")
        return code, message, retryable, provider, details
    return _status_code_to_code(exc.status_code), str(detail), exc.status_code >= 500, None, None


def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code, message, retryable, provider, details = _coerce_http_exception_details(exc)
    if exc.status_code >= 500:
        logger.error(
            "HTTP exception returned to client",
            extra={"extra_json": {"status_code": exc.status_code, "code": code, "provider": provider}},
        )
    else:
        logger.warning(
            "Client-facing HTTP exception returned",
            extra={"extra_json": {"status_code": exc.status_code, "code": code, "provider": provider}},
        )
    return error_response(
        request=request,
        status_code=exc.status_code,
        code=code,
        message=message,
        retryable=retryable,
        provider=provider,
        details=details,
    )


def request_validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="validation_error",
        message="Request validation failed",
        retryable=False,
        details=exc.errors(),
    )


def api_service_exception_handler(request: Request, exc: ApiServiceError) -> JSONResponse:
    logger.error(
        "Typed service exception returned",
        extra={
            "extra_json": {
                "status_code": exc.status_code,
                "code": exc.code,
                "provider": exc.provider,
                "retryable": exc.retryable,
            }
        },
    )
    return error_response(
        request=request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        provider=exc.provider,
        details=exc.details or None,
    )


def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception returned", extra={"extra_json": {"exception_type": exc.__class__.__name__}})
    return error_response(
        request=request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="internal_error",
        message="An unexpected server error occurred",
        retryable=False,
    )
