from __future__ import annotations

import json
from urllib import error, request

from app.settings import settings


class VectorStoreError(RuntimeError):
    pass


def ensure_collection(vector_size: int) -> None:
    response = _request("GET", _collection_url())
    result = response.get("result")
    if result:
        size = ((result.get("config") or {}).get("params") or {}).get("vectors", {}).get("size")
        if size is not None and int(size) != vector_size:
            raise VectorStoreError(
                f"Qdrant collection '{settings.qdrant_collection_name}' has vector size {size}, expected {vector_size}"
            )
        return
    _request(
        "PUT",
        _collection_url(),
        {
            "vectors": {
                "size": vector_size,
                "distance": "Cosine",
            }
        },
    )


def upsert_points(points: list[dict]) -> None:
    if not points:
        return
    _request("PUT", f"{_collection_url()}/points", {"points": points})


def delete_points(point_ids: list[str]) -> None:
    if not point_ids:
        return
    _request("POST", f"{_collection_url()}/points/delete", {"points": point_ids})


def _request(method: str, url: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(url, data=body, method=method, headers=_headers())
    try:
        with request.urlopen(req, timeout=settings.qdrant_timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:  # pragma: no cover - network/provider failure
        if method == "GET" and exc.code == 404:
            return {}
        detail = exc.read().decode("utf-8", errors="replace")
        raise VectorStoreError(f"Qdrant request failed with status {exc.code}: {detail}") from exc
    except error.URLError as exc:  # pragma: no cover - network/provider failure
        raise VectorStoreError(f"Qdrant request failed: {exc.reason}") from exc
    return json.loads(raw) if raw else {}


def _collection_url() -> str:
    return f"{settings.qdrant_url.rstrip('/')}/collections/{settings.qdrant_collection_name}"


def _headers() -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if settings.qdrant_api_key:
        headers["Api-Key"] = settings.qdrant_api_key
    return headers
