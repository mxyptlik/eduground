from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import (
    ApiServiceError,
    api_service_exception_handler,
    http_exception_handler,
    request_validation_exception_handler,
    unhandled_exception_handler,
)
from app.core.logging import configure_logging
from app.core.middleware import AuthRateLimitMiddleware, RequestContextMiddleware, RequestSizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.observability import configure_observability, metrics_registry
from app.core.readiness import collect_dependency_checks
from app.core.runtime import validate_runtime_configuration
from app.db.init_db import initialize_database
from app.schemas.common import DependencyStatusResponse, HealthResponse, ReadinessResponse

configure_logging("api")
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_runtime_configuration()
    configure_observability(service="api", fastapi_app=app)
    initialize_database()
    try:
        yield
    finally:
        from app.integrations.connections import close_connections
        close_connections()

app = FastAPI(
    title="Curriculum Tutor API",
    version="0.1.0",
    description="RAG-first tutoring backend with notebook scoping and citations.",
    lifespan=lifespan,
)


@app.middleware("http")
async def internal_errors_keep_cors_headers(request: Request, call_next):
    """Convert unhandled exceptions into a 500 that still carries CORS headers.

    Starlette handles ``Exception`` in ServerErrorMiddleware, which sits outside
    the CORS middleware, so a bare 500 reaches the browser without
    ``Access-Control-Allow-Origin`` and surfaces as a misleading CORS/network
    failure. Catching here (inside CORS) keeps the real status visible.
    """
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled application error", extra={"extra_json": {"path": request.url.path}})
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "Internal server error.",
                "retryable": False,
                "request_id": getattr(request.state, "request_id", None),
            },
        )


if settings.app_env.lower() in {"production", "staging"}:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list or ["localhost", "127.0.0.1"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(AuthRateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
if settings.app_env.lower() in {"production", "staging"}:
    app.add_middleware(HTTPSRedirectMiddleware)
app.include_router(api_router, prefix=settings.api_prefix)
app.add_exception_handler(ApiServiceError, api_service_exception_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, request_validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


@app.get("/health", tags=["health"], response_model=HealthResponse)
def healthcheck(request: Request) -> HealthResponse:
    return HealthResponse(
        status="ok",
        environment=settings.app_env,
        app_name=settings.app_name,
        request_id=getattr(request.state, "request_id", None),
    )


@app.get(
    "/ready",
    tags=["health"],
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
def readiness(request: Request):
    dependencies = collect_dependency_checks()
    is_ready = all(item.healthy for item in dependencies)
    response = ReadinessResponse(
        status="ready" if is_ready else "degraded",
        environment=settings.app_env,
        app_name=settings.app_name,
        request_id=getattr(request.state, "request_id", None),
        dependencies=[
            DependencyStatusResponse(
                name=item.name,
                healthy=item.healthy,
                latency_ms=item.latency_ms,
                message=item.message,
                details=item.details,
            )
            for item in dependencies
        ],
    )
    if not is_ready:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=response.model_dump(mode="json"))
    return response


@app.get("/metrics", tags=["health"], response_class=PlainTextResponse)
def metrics() -> str:
    return metrics_registry.render_prometheus()
