from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json
from urllib import error, request

from app.core.config import settings
from app.core.errors import DependencyUnavailableError
from app.core.observability import traced_operation
from app.integrations.vectorstore.base import VectorStore, VectorStoreConfig, VectorStoreError


def _request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    *,
    headers: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with traced_operation(
            "provider.qdrant.request",
            metric_name="eduground_provider_call",
            metric_labels={"provider": "qdrant", "operation": method.lower()},
        ):
            with request.urlopen(req, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
    except error.HTTPError as exc:  # pragma: no cover - exercised through integration tests
        detail = exc.read().decode("utf-8", errors="replace")
        raise VectorStoreError(
            f"Qdrant request failed with status {exc.code}: {detail}",
            status_code=502 if exc.code >= 500 else 424,
            retryable=exc.code >= 500 or exc.code == 429,
            details={"upstream_status_code": exc.code},
        ) from exc
    except error.URLError as exc:  # pragma: no cover - exercised through integration tests
        raise VectorStoreError(f"Qdrant request failed: {exc.reason}") from exc
    return json.loads(raw) if raw else {}


def _headers(config: VectorStoreConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if config.api_key:
        headers["Api-Key"] = config.api_key
    headers.update(config.extra_headers)
    return headers


def _qdrant_filter(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    if not filters:
        return None
    must: list[dict[str, Any]] = []
    key_map = {
        "source_ids": "source_id",
        "module_ids": "module_id",
    }
    for key, value in filters.items():
        if value is None:
            continue
        mapped_key = key_map.get(key, key)
        if isinstance(value, dict):
            must.append({mapped_key: value})
        elif isinstance(value, (list, tuple, set)):
            must.append({"key": mapped_key, "match": {"any": list(value)}})
        else:
            must.append({"key": mapped_key, "match": {"value": value}})
    return {"must": must} if must else None


def _parse_collection_size(response: dict[str, Any]) -> int | None:
    result = response.get("result") or {}
    config = result.get("config") or {}
    params = config.get("params") or {}
    vectors = params.get("vectors") or {}
    if isinstance(vectors, dict):
        size = vectors.get("size")
        if isinstance(size, int):
            return size
    return None


@dataclass(slots=True)
class QdrantClientConfig:
    url: str
    api_key: str | None
    collection_name: str
    timeout_seconds: float
    vector_size: int | None
    distance: str

    @classmethod
    def from_env(cls) -> "QdrantClientConfig":
        return cls(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            collection_name=settings.qdrant_collection_name,
            timeout_seconds=60.0,
            vector_size=None,
            distance="Cosine",
        )


class QdrantVectorStore(VectorStore):
    def __init__(self, config: QdrantClientConfig | None = None) -> None:
        client_config = config or QdrantClientConfig.from_env()
        if not client_config.url:
            raise DependencyUnavailableError(
                code="qdrant_url_missing",
                message="CURRICULUM_TUTOR_QDRANT_URL is required for the Qdrant vector store",
                provider="qdrant",
            )
        self.config = VectorStoreConfig(
            url=client_config.url,
            api_key=client_config.api_key,
            collection_name=client_config.collection_name,
            timeout_seconds=client_config.timeout_seconds,
            vector_size=client_config.vector_size,
            distance=client_config.distance,
        )

    def upsert(self, point_id: str, vector: list[float], payload: dict) -> None:
        self._ensure_collection(vector_size=len(vector))
        body = {
            "points": [
                {
                    "id": point_id,
                    "vector": vector,
                    "payload": payload,
                }
            ]
        }
        _request_json(
            "PUT",
            f"{self.config.url.rstrip('/')}/collections/{self.config.collection_name}/points?wait=true",
            body,
            headers=_headers(self.config),
            timeout_seconds=self.config.timeout_seconds,
        )

    def search(self, vector: list[float], filters: dict, top_k: int) -> list[dict]:
        self._ensure_collection()
        body: dict[str, Any] = {
            "vector": vector,
            "limit": top_k,
            "with_payload": True,
            "with_vector": False,
        }
        qdrant_filter = _qdrant_filter(filters)
        if qdrant_filter:
            body["filter"] = qdrant_filter
        response = _request_json(
            "POST",
            f"{self.config.url.rstrip('/')}/collections/{self.config.collection_name}/points/search",
            body,
            headers=_headers(self.config),
            timeout_seconds=self.config.timeout_seconds,
        )
        points = response.get("result") or []
        results: list[dict] = []
        for point in points:
            if not isinstance(point, dict):
                continue
            results.append(
                {
                    "point_id": point.get("id"),
                    "score": point.get("score"),
                    "payload": point.get("payload") or {},
                    "vector": point.get("vector"),
                }
            )
        return results

    def delete_points(self, point_ids: list[str]) -> None:
        if not point_ids:
            return
        self._ensure_collection()
        _request_json(
            "POST",
            f"{self.config.url.rstrip('/')}/collections/{self.config.collection_name}/points/delete?wait=true",
            {"points": point_ids},
            headers=_headers(self.config),
            timeout_seconds=self.config.timeout_seconds,
        )

    def _ensure_collection(self, *, vector_size: int | None = None) -> None:
        current_size = vector_size or self.config.vector_size
        if current_size is None:
            try:
                existing = _request_json(
                    "GET",
                    f"{self.config.url.rstrip('/')}/collections/{self.config.collection_name}",
                    None,
                    headers=_headers(self.config),
                    timeout_seconds=self.config.timeout_seconds,
                )
            except VectorStoreError:
                raise VectorStoreError(
                    "Qdrant collection does not exist and vector size is unknown; set QDRANT_VECTOR_SIZE or upsert a vector first."
                ) from None
            existing_size = _parse_collection_size(existing)
            if existing_size is None:
                return
            self.config.vector_size = existing_size
            return

        try:
            existing = _request_json(
                "GET",
                f"{self.config.url.rstrip('/')}/collections/{self.config.collection_name}",
                None,
                headers=_headers(self.config),
                timeout_seconds=self.config.timeout_seconds,
            )
        except VectorStoreError:
            body = {
                "vectors": {
                    "size": current_size,
                    "distance": self.config.distance,
                }
            }
            _request_json(
                "PUT",
                f"{self.config.url.rstrip('/')}/collections/{self.config.collection_name}",
                body,
                headers=_headers(self.config),
                timeout_seconds=self.config.timeout_seconds,
            )
            self.config.vector_size = current_size
            return

        existing_size = _parse_collection_size(existing)
        if existing_size is not None and existing_size != current_size:
            raise VectorStoreError(
                f"Qdrant collection {self.config.collection_name!r} already exists with vector size {existing_size}, "
                f"but the provided vector has size {current_size}."
            )
        self.config.vector_size = existing_size or current_size
