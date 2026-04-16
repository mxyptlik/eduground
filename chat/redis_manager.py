import os
import json
import logging
from typing import Optional, List # Added List and Optional
from dotenv import load_dotenv
import redis.asyncio as aioredis
import time
import asyncio
from langchain_core.chat_history import BaseChatMessageHistory # Corrected import for the BASE class
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, messages_from_dict, messages_to_dict

load_dotenv()
logger = logging.getLogger(__name__)

class AsyncRedisChatMessageHistory(BaseChatMessageHistory): # This now inherits from the imported base class
    """Async chat message history backed by Redis."""

    def __init__(
        self,
        session_id: str,
        redis_client: aioredis.Redis,
        key_prefix: str = "chat_session:",
        ttl: Optional[int] = None,
    ):
        self.redis_client = redis_client
        self.session_id = session_id
        self.key = f"{key_prefix}{session_id}"
        self.ttl = ttl

    async def aget_messages(self) -> List[BaseMessage]:
        """Retrieve messages from Redis."""
        try:
            stored_session = await self.redis_client.get(self.key)
            if stored_session:
                # Ensure stored_session is a string before json.loads
                items_str = stored_session
                if isinstance(items_str, bytes):
                    items_str = items_str.decode('utf-8') # Decode if bytes
                items = json.loads(items_str)
                messages = messages_from_dict(items)
                return messages
            return []
        except Exception as e:
            logger.error(f"Error retrieving messages for session {self.session_id} from Redis: {e}")
            return []


    async def _save_messages_internal(self, messages: List[BaseMessage]) -> None: # Renamed to avoid conflict
        """Save messages to Redis."""
        try:
            await self.redis_client.set(self.key, json.dumps(messages_to_dict(messages)))
            if self.ttl:
                await self.redis_client.expire(self.key, self.ttl)
        except Exception as e:
            logger.error(f"Error saving messages for session {self.session_id} to Redis: {e}")

    async def aadd_message(self, message: BaseMessage) -> None:
        """Append a message to the history and save."""
        messages = await self.aget_messages()
        messages.append(message)
        await self._save_messages_internal(messages)

    async def aadd_messages(self, messages_to_add: List[BaseMessage]) -> None:
        """Append multiple messages to the history and save."""
        current_messages = await self.aget_messages()
        current_messages.extend(messages_to_add)
        await self._save_messages_internal(current_messages)

    async def aadd_user_message(self, message_content: str) -> None: # Parameter renamed for clarity
        """Convenience method to add a human message string."""
        await self.aadd_message(HumanMessage(content=message_content))

    async def aadd_ai_message(self, message_content: str) -> None: # Parameter renamed for clarity
        """Convenience method to add an AI message string."""
        await self.aadd_message(AIMessage(content=message_content))

    async def aclear(self) -> None:
        """Clear session history from Redis."""
        try:
            await self.redis_client.delete(self.key)
        except Exception as e:
            logger.error(f"Error clearing session {self.session_id} from Redis: {e}")

    # ---- Synchronous methods required by BaseChatMessageHistory ----

    @property
    def messages(self) -> List[BaseMessage]:
        """Retrieve messages synchronously. Not recommended for this async class."""
        logger.error(f"Synchronous 'messages' property access for session {self.session_id} on AsyncRedisChatMessageHistory is not supported. Use 'aget_messages'.")
        raise NotImplementedError("Use aget_messages() for async access to messages.")

    def add_message(self, message: BaseMessage) -> None:
        """Add a message synchronously. Not recommended for this async class."""
        logger.error(f"Synchronous 'add_message' for session {self.session_id} on AsyncRedisChatMessageHistory is not supported. Use 'aadd_message'.")
        raise NotImplementedError("Use aadd_message() for async message addition.")

    def clear(self) -> None:
        """Clear history synchronously. Not recommended for this async class."""
        logger.error(f"Synchronous 'clear' for session {self.session_id} on AsyncRedisChatMessageHistory is not supported. Use 'aclear'.")
        raise NotImplementedError("Use aclear() for async history clearing.")


class RedisSessionManager:
    _instance = None
    _redis_pool = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(RedisSessionManager, cls).__new__(cls)
        return cls._instance

    def __init__(self, redis_url: Optional[str] = None, ttl: int = 3600):
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        self.redis_url = redis_url or os.getenv("REDIS_URL")
        if not self.redis_url:
            raise ValueError("Redis URL not provided or found in environment variables.")
        
        self.ttl = ttl
        self._initialized = True
        logger.info(f"RedisSessionManager initialized with URL: {self.redis_url} and TTL: {self.ttl}s")


    async def get_client(self) -> aioredis.Redis:
        if self._redis_pool is None:
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # decode_responses=True makes redis client return strings instead of bytes
                    self._redis_pool = aioredis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
                    await self._redis_pool.ping()
                    logger.info(f"Successfully connected to Redis and created connection pool on attempt {attempt+1}.")
                    break
                except Exception as e:
                    logger.warning(f"Redis connection attempt {attempt + 1} failed: {e}")
                    if self._redis_pool:
                        try:
                            await self._redis_pool.close()
                        except:
                            pass    
                    self._redis_pool = None 
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)
                    else:
                       # All attempts failed
                        logger.error(f"Failed to connect to Redis after {max_retries} attempts")
                        raise ConnectionError(f"Could not connect to Redis after {max_retries} attempts")        
        return self._redis_pool

    async def get_session_history(self, session_id: str) -> AsyncRedisChatMessageHistory:
        client = await self.get_client()
        return AsyncRedisChatMessageHistory(session_id, client, ttl=self.ttl)

    async def delete_history(self, session_id: str):
        """Deletes a session's chat history."""
        history = await self.get_session_history(session_id)
        await history.aclear()
        logger.info(f"Deleted chat history for session: {session_id}")

    # save_session_history is removed as AsyncRedisChatMessageHistory now handles its own persistence.
    # RunnableWithMessageHistory will call aadd_messages on the history object.

    async def check_connection_health(self) -> bool:
        client = None
        try:
            client = await self.get_client()
            await client.ping()
            logger.info("Redis connection health check successful.")
            return True
        except Exception as e:
            logger.error(f"Redis connection health check failed: {e}")
            if self._redis_pool:
                 await self._redis_pool.close() 
            self._redis_pool = None 
            return False

    async def close(self):
        if self._redis_pool:
            try:
                await self._redis_pool.close()
                logger.info("Redis connection pool closed.")
            except Exception as e:
                logger.error(f"Error closing Redis connection pool: {e}")
            finally:
                self._redis_pool = None
