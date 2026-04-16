from __future__ import annotations

from types import SimpleNamespace

from app.models.enums import ChatRole, MessageStatus
from app.services.chat_memory import ChatMemoryStore


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

    def delete(self, key: str):
        self.store.pop(key, None)
        self.expiry.pop(key, None)

    def expire(self, key: str, seconds: int):
        self.expiry[key] = seconds


class FakeRepository:
    def __init__(self, messages):
        self._messages = messages

    def list_messages_for_session(self, session_id: str):
        return self._messages


def test_chat_memory_replays_sql_messages_and_sets_ttl():
    repository = FakeRepository(
        [
            SimpleNamespace(role=ChatRole.USER, content_markdown="Hello", status=MessageStatus.COMPLETE),
            SimpleNamespace(role=ChatRole.ASSISTANT, content_markdown="Hi there", status=MessageStatus.COMPLETE),
        ]
    )
    redis_client = FakeRedis()

    store = ChatMemoryStore(repository, redis_client=redis_client)
    snapshot = store.get_session_history("session-1")

    assert snapshot.hydrated_from_sql is True
    assert len(snapshot.messages) == 2
    assert "eduground:chat:history:session-1" in redis_client.store
    assert redis_client.expiry["eduground:chat:history:session-1"] > 0


def test_chat_memory_uses_existing_redis_history_before_sql():
    repository = FakeRepository([])
    redis_client = FakeRedis()
    store = ChatMemoryStore(repository, redis_client=redis_client)
    store.append_user_message("session-2", "Question")

    snapshot = store.get_session_history("session-2")

    assert snapshot.hydrated_from_sql is False
    assert len(snapshot.messages) == 1
    assert redis_client.expiry["eduground:chat:history:session-2"] > 0


def test_chat_memory_refreshes_ttl_on_read():
    repository = FakeRepository([])
    redis_client = FakeRedis()
    store = ChatMemoryStore(repository, redis_client=redis_client)
    store.append_user_message("session-3", "Question")
    redis_client.expiry["eduground:chat:history:session-3"] = 1

    store.get_session_history("session-3")

    assert redis_client.expiry["eduground:chat:history:session-3"] > 1
