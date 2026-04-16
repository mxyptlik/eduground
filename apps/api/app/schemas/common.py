from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TimestampedResponse(ORMModel):
    id: str
    created_at: datetime


class ApiErrorResponse(BaseModel):
    code: str
    message: str
    detail: str | None = None
    retryable: bool
    request_id: str | None = None
    provider: str | None = None
    details: dict | list | None = None


class DependencyStatusResponse(BaseModel):
    name: str
    healthy: bool
    latency_ms: int
    message: str
    details: dict


class HealthResponse(BaseModel):
    status: str
    environment: str
    app_name: str
    request_id: str | None = None


class ReadinessResponse(BaseModel):
    status: str
    environment: str
    app_name: str
    request_id: str | None = None
    dependencies: list[DependencyStatusResponse]
