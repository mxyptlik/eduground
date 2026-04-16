from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("app.chat_retrieval_cache")


@dataclass(slots=True)
class CachedCandidate:
    chunk_id: str
    source_id: str
    score: float
    rank_before: int
    rank_after: int
    rerank_score: float | None = None


class ChatRetrievalCacheStore:
    def __init__(self, redis_client: Redis | None = None) -> None:
        self._redis_client = redis_client
        self._redis_failed = False

    def get(self, *, session_id: str, query_text: str, filters: dict[str, Any]) -> list[CachedCandidate] | None:
        client = self._client()
        if client is None:
            return None
        key = self._key(session_id=session_id, query_text=query_text, filters=filters)
        try:
            raw = client.get(key)
        except RedisError:
            self._mark_redis_failed("Failed to load tutor chat retrieval cache")
            return None
        if not raw:
            return None
        self._refresh_ttl(client, key)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Tutor chat retrieval cache was not valid JSON", extra={"extra_json": {"session_id": session_id}})
            return None
        if not isinstance(payload, list):
            return None
        cached: list[CachedCandidate] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                cached.append(
                    CachedCandidate(
                        chunk_id=str(item["chunk_id"]),
                        source_id=str(item["source_id"]),
                        score=float(item["score"]),
                        rank_before=int(item["rank_before"]),
                        rank_after=int(item["rank_after"]),
                        rerank_score=float(item["rerank_score"]) if item.get("rerank_score") is not None else None,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return cached or None

    def set(
        self,
        *,
        session_id: str,
        query_text: str,
        filters: dict[str, Any],
        candidates: Sequence[CachedCandidate],
    ) -> None:
        client = self._client()
        if client is None:
            return
        key = self._key(session_id=session_id, query_text=query_text, filters=filters)
        payload = json.dumps(
            [
                {
                    "chunk_id": candidate.chunk_id,
                    "source_id": candidate.source_id,
                    "score": candidate.score,
                    "rank_before": candidate.rank_before,
                    "rank_after": candidate.rank_after,
                    "rerank_score": candidate.rerank_score,
                }
                for candidate in candidates
            ]
        )
        try:
            client.set(key, payload, ex=settings.chat_memory_ttl_seconds)
        except RedisError:
            self._mark_redis_failed("Failed to persist tutor chat retrieval cache")

    def _key(self, *, session_id: str, query_text: str, filters: dict[str, Any]) -> str:
        normalized_query = " ".join(query_text.strip().lower().split())
        serialized_filters = json.dumps(filters, sort_keys=True, separators=(",", ":"))
        cache_hash = hashlib.sha256(f"{normalized_query}|{serialized_filters}".encode("utf-8")).hexdigest()
        return f"eduground:chat:retrieval:{session_id}:{cache_hash}"

    def _client(self) -> Redis | None:
        if self._redis_failed:
            return None
        if self._redis_client is not None:
            return self._redis_client
        try:
            self._redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
            self._redis_client.ping()
            return self._redis_client
        except RedisError:
            self._mark_redis_failed("Redis is unavailable for tutor chat retrieval cache")
            return None

    def _refresh_ttl(self, client: Redis, key: str) -> None:
        try:
            client.expire(key, settings.chat_memory_ttl_seconds)
        except RedisError:
            self._mark_redis_failed("Failed to refresh tutor chat retrieval cache TTL")

    def _mark_redis_failed(self, message: str) -> None:
        self._redis_failed = True
        logger.warning(message)
