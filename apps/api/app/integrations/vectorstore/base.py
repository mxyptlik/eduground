from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ProviderRequestError


class VectorStoreError(ProviderRequestError):
    def __init__(self, message: str, *, status_code: int = 502, retryable: bool = True, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="qdrant_vectorstore_error",
            message=message,
            status_code=status_code,
            retryable=retryable,
            provider="qdrant",
            details=details or {},
        )


@dataclass(slots=True)
class VectorStoreConfig:
    url: str | None = None
    api_key: str | None = None
    collection_name: str = "curriculum_chunks"
    timeout_seconds: float = 60.0
    vector_size: int | None = None
    distance: str = "Cosine"
    extra_headers: dict[str, str] = field(default_factory=dict)


def normalize_vector_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    return payload or {}


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, point_id: str, vector: list[float], payload: dict) -> None:
        raise NotImplementedError

    @abstractmethod
    def search(self, vector: list[float], filters: dict, top_k: int) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    def delete_points(self, point_ids: list[str]) -> None:
        raise NotImplementedError
