from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.models.enums import ChatRole, MessageStatus
from app.repositories.chat import ChatRepository

logger = get_logger("app.chat_memory")

@dataclass(slots=True)
class StoredChatMessage:
    type: str
    content: str


def _human_message(content: str) -> StoredChatMessage:
    return StoredChatMessage(type="human", content=content)


def _ai_message(content: str) -> StoredChatMessage:
    return StoredChatMessage(type="ai", content=content)


@dataclass(slots=True)
class ChatMemorySnapshot:
    messages: list[StoredChatMessage]
    hydrated_from_sql: bool
    redis_available: bool


class ChatMemoryStore:
    def __init__(self, repository: ChatRepository, redis_client: Redis | None = None) -> None:
        self.repository = repository
        self._redis_client = redis_client
        self._redis_failed = False

    def get_session_history(self, session_id: str) -> ChatMemorySnapshot:
        stored_messages = self._load_messages_from_redis(session_id)
        if stored_messages is not None:
            return ChatMemorySnapshot(messages=stored_messages, hydrated_from_sql=False, redis_available=True)

        replayed_messages = self._replay_messages_from_sql(session_id)
        if replayed_messages:
            self._store_messages(session_id, replayed_messages)
        return ChatMemorySnapshot(
            messages=replayed_messages,
            hydrated_from_sql=bool(replayed_messages),
            redis_available=self._redis_client is not None and not self._redis_failed,
        )

    def append_user_message(self, session_id: str, content: str) -> None:
        snapshot = self.get_session_history(session_id)
        snapshot.messages.append(_human_message(content))
        self._store_messages(session_id, snapshot.messages)

    def append_ai_message(self, session_id: str, content: str) -> None:
        snapshot = self.get_session_history(session_id)
        snapshot.messages.append(_ai_message(content))
        self._store_messages(session_id, snapshot.messages)

    def replace_session_history(self, session_id: str, messages: Sequence[StoredChatMessage]) -> None:
        self._store_messages(session_id, list(messages))

    def refresh_session_history(self, session_id: str) -> list[StoredChatMessage]:
        replayed_messages = self._replay_messages_from_sql(session_id)
        if replayed_messages:
            self._store_messages(session_id, replayed_messages)
        return replayed_messages

    def clear_session_history(self, session_id: str) -> None:
        client = self._client()
        if client is None:
            return
        try:
            client.delete(self._key(session_id))
        except RedisError:
            self._mark_redis_failed("Failed to clear tutor chat memory")

    def _load_messages_from_redis(self, session_id: str) -> list[StoredChatMessage] | None:
        client = self._client()
        if client is None:
            return None
        try:
            raw = client.get(self._key(session_id))
        except RedisError:
            self._mark_redis_failed("Failed to load tutor chat memory")
            return None
        if not raw:
            return None
        self._refresh_ttl(client, session_id)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Redis tutor chat memory was not valid JSON", extra={"extra_json": {"session_id": session_id}})
            return None
        if not isinstance(payload, list):
            return None
        stored_messages: list[StoredChatMessage] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            message_type = item.get("type")
            content = item.get("content")
            if isinstance(message_type, str) and isinstance(content, str):
                stored_messages.append(StoredChatMessage(type=message_type, content=content))
        return stored_messages

    def _store_messages(self, session_id: str, messages: Sequence[StoredChatMessage]) -> None:
        client = self._client()
        if client is None:
            return
        try:
            payload = json.dumps([{"type": message.type, "content": message.content} for message in messages])
            client.set(self._key(session_id), payload, ex=settings.chat_memory_ttl_seconds)
        except RedisError:
            self._mark_redis_failed("Failed to persist tutor chat memory")

    def _replay_messages_from_sql(self, session_id: str) -> list[StoredChatMessage]:
        sql_messages = self.repository.list_messages_for_session(session_id)
        replayed: list[StoredChatMessage] = []
        for message in sql_messages:
            if message.status not in {MessageStatus.COMPLETE, MessageStatus.STREAMING}:
                continue
            if message.role == ChatRole.USER:
                replayed.append(_human_message(message.content_markdown))
            elif message.role == ChatRole.ASSISTANT and message.content_markdown.strip():
                replayed.append(_ai_message(message.content_markdown))
        return replayed

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
            self._mark_redis_failed("Redis is unavailable for tutor chat memory")
            return None

    def _key(self, session_id: str) -> str:
        return f"eduground:chat:history:{session_id}"

    def _refresh_ttl(self, client: Redis, session_id: str) -> None:
        try:
            client.expire(self._key(session_id), settings.chat_memory_ttl_seconds)
        except RedisError:
            self._mark_redis_failed("Failed to refresh tutor chat memory TTL")

    def _mark_redis_failed(self, message: str) -> None:
        self._redis_failed = True
        logger.warning(message)


def message_to_openai_dict(message: StoredChatMessage | Any) -> dict[str, str]:
    role = getattr(message, "type", None)
    role_map = {
        "human": "user",
        "ai": "assistant",
        "system": "system",
    }
    return {"role": role_map.get(role, "user"), "content": str(getattr(message, "content", ""))}
