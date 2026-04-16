from __future__ import annotations

from app.services.chat_retrieval_cache import CachedCandidate, ChatRetrievalCacheStore


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiry: dict[str, int] = {}

    def ping(self) -> bool:
        return True

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        if ex is not None:
            self.expiry[key] = ex

    def expire(self, key: str, seconds: int):
        self.expiry[key] = seconds


def test_retrieval_cache_round_trips_candidates_and_refreshes_ttl():
    redis_client = FakeRedis()
    cache = ChatRetrievalCacheStore(redis_client=redis_client)
    filters = {"notebook_id": "notebook-1", "status": "indexed", "source_ids": ["source-1"]}

    cache.set(
        session_id="session-1",
        query_text="What is mitosis?",
        filters=filters,
        candidates=[
            CachedCandidate(
                chunk_id="chunk-1",
                source_id="source-1",
                score=0.88,
                rank_before=1,
                rank_after=1,
                rerank_score=0.92,
            )
        ],
    )

    key = next(iter(redis_client.store))
    redis_client.expiry[key] = 1

    cached = cache.get(session_id="session-1", query_text="  What is   mitosis? ", filters=filters)

    assert cached is not None
    assert len(cached) == 1
    assert cached[0].chunk_id == "chunk-1"
    assert redis_client.expiry[key] > 1
