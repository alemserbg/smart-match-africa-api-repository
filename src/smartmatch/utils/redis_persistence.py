import asyncio
import json
import logging
from collections import defaultdict
from typing import Any, DefaultDict, Dict, Optional, Tuple, Union

from telegram.ext import BasePersistence, PersistenceInput

logger = logging.getLogger(__name__)


class SetEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, set):
            return list(obj)
        return super().default(obj)


class RedisPersistence(BasePersistence):
    """
    Production-grade Redis persistence layer for python-telegram-bot (v20+).
    Guarantees strict multi-user conversation isolation and zero state leak across
    concurrent processes via deterministic tuple key serialization and Redis Hashes.
    """

    def __init__(
        self,
        redis_client: Any,
        store_data: Optional[PersistenceInput] = None,
        update_interval: float = 60.0,
    ) -> None:
        store = store_data or PersistenceInput(
            bot_data=True,
            chat_data=True,
            user_data=True,
            callback_data=True,
        )
        super().__init__(store_data=store, update_interval=update_interval)
        self.redis = redis_client

    @staticmethod
    async def _with_retry(coro_factory):
        """Executes Redis operations with exponential backoff retry for transient network/DNS failures."""
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                return await coro_factory()
            except Exception as e:
                err_str = str(e).lower()
                is_transient = any(kw in err_str for kw in [
                    "semaphore timeout", "error 22", "error 11001", "getaddrinfo", "timeout", "connection", "reset", "closed", "socket"
                ])
                if attempt == max_retries or not is_transient:
                    raise
                logger.warning(f"⚠️ Redis transient connection/DNS fault (Attempt {attempt}/{max_retries}): {e}. Retrying...")
                await asyncio.sleep(1.5 ** attempt)

    @staticmethod
    def _safe_decode(val: Union[bytes, str, None]) -> Optional[str]:
        """Ensures byte responses from Redis are decoded cleanly regardless of driver mode."""
        if val is None:
            return None
        if isinstance(val, bytes):
            return val.decode("utf-8")
        return str(val)

    @classmethod
    def _safe_json_loads(cls, raw_data: Optional[Union[bytes, str]]) -> Any:
        """Safely decodes and deserializes JSON payloads."""
        decoded = cls._safe_decode(raw_data)
        if not decoded:
            return None
        try:
            return json.loads(decoded)
        except (json.JSONDecodeError, TypeError) as err:
            logger.error(f"Failed to decode Redis JSON payload: {err}")
            return None

    @classmethod
    def _serialize_conv_key(cls, key: Tuple[int, ...]) -> str:
        """
        Deterministically serializes PTB conversation tuple keys (e.g., (chat_id, user_id))
        into non-ambiguous JSON string keys to prevent cross-talk collisions.
        """
        return json.dumps(list(key))

    @classmethod
    def _deserialize_conv_key(cls, raw_key: str) -> Optional[Tuple[int, ...]]:
        """
        Safely restores integer tuple keys from JSON or legacy string reprs.
        """
        try:
            parsed = json.loads(raw_key)
            if isinstance(parsed, list):
                return tuple(int(x) for x in parsed)
        except Exception:
            pass

        # Fallback legacy support for Python stringified tuples e.g. "(12345, 67890)"
        try:
            import ast
            res = ast.literal_eval(raw_key)
            if isinstance(res, tuple):
                return tuple(int(x) for x in res)
        except Exception:
            pass

        logger.error(f"Failed to deserialize conversation key: {raw_key}")
        return None

    # -------------------------------------------------------------------------
    # USER DATA (Self-Healing on WRONGTYPE)
    # -------------------------------------------------------------------------

    async def get_user_data(self) -> DefaultDict[int, Dict[str, Any]]:
        try:
            data = await self._with_retry(lambda: self.redis.hgetall("ptb:user_data"))
            result: DefaultDict[int, Dict[str, Any]] = defaultdict(dict)
            if data:
                for k, v in data.items():
                    try:
                        user_id = int(self._safe_decode(k) or 0)
                        user_dict = self._safe_json_loads(v)
                        if isinstance(user_dict, dict):
                            result[user_id] = user_dict
                    except (ValueError, TypeError) as err:
                        logger.error(f"Skipping malformed user_data key {k}: {err}")
            return result
        except Exception as e:
            if "WRONGTYPE" in str(e):
                logger.warning("⚠️ Legacy string key detected on get_user_data. Purging 'ptb:user_data'...")
                await self._with_retry(lambda: self.redis.delete("ptb:user_data"))
            else:
                logger.error(f"Error fetching user_data from Redis: {e}")
            return defaultdict(dict)

    async def update_user_data(self, user_id: int, data: Dict[str, Any]) -> None:
        try:
            await self._with_retry(lambda: self.redis.hset(
                "ptb:user_data",
                mapping={str(int(user_id)): json.dumps(data)}
            ))
        except Exception as e:
            if "WRONGTYPE" in str(e):
                logger.warning(f"⚠️ Overwriting legacy key type for 'ptb:user_data' on user {user_id}...")
                await self._with_retry(lambda: self.redis.delete("ptb:user_data"))
                await self._with_retry(lambda: self.redis.hset(
                    "ptb:user_data",
                    mapping={str(int(user_id)): json.dumps(data)}
                ))
            else:
                logger.error(f"Error updating user_data for user_id {user_id}: {e}")

    async def refresh_user_data(self, user_id: int, user_data: Dict[str, Any]) -> None:
        pass

    async def drop_user_data(self, user_id: int) -> None:
        try:
            await self._with_retry(lambda: self.redis.hdel("ptb:user_data", str(int(user_id))))
        except Exception as e:
            logger.error(f"Error dropping user_data for user_id {user_id}: {e}")

    # -------------------------------------------------------------------------
    # CHAT DATA (Self-Healing on WRONGTYPE)
    # -------------------------------------------------------------------------

    async def get_chat_data(self) -> DefaultDict[int, Dict[str, Any]]:
        try:
            data = await self._with_retry(lambda: self.redis.hgetall("ptb:chat_data"))
            result: DefaultDict[int, Dict[str, Any]] = defaultdict(dict)
            if data:
                for k, v in data.items():
                    try:
                        chat_id = int(self._safe_decode(k) or 0)
                        chat_dict = self._safe_json_loads(v)
                        if isinstance(chat_dict, dict):
                            result[chat_id] = chat_dict
                    except (ValueError, TypeError) as err:
                        logger.error(f"Skipping malformed chat_data key {k}: {err}")
            return result
        except Exception as e:
            if "WRONGTYPE" in str(e):
                logger.warning("⚠️ Legacy string key detected on get_chat_data. Purging 'ptb:chat_data'...")
                await self._with_retry(lambda: self.redis.delete("ptb:chat_data"))
            else:
                logger.error(f"Error fetching chat_data from Redis: {e}")
            return defaultdict(dict)

    async def update_chat_data(self, chat_id: int, data: Dict[str, Any]) -> None:
        try:
            await self._with_retry(lambda: self.redis.hset(
                "ptb:chat_data",
                mapping={str(int(chat_id)): json.dumps(data)}
            ))
        except Exception as e:
            if "WRONGTYPE" in str(e):
                logger.warning(f"⚠️ Overwriting legacy key type for 'ptb:chat_data' on chat {chat_id}...")
                await self._with_retry(lambda: self.redis.delete("ptb:chat_data"))
                await self._with_retry(lambda: self.redis.hset(
                    "ptb:chat_data",
                    mapping={str(int(chat_id)): json.dumps(data)}
                ))
            else:
                logger.error(f"Error updating chat_data for chat_id {chat_id}: {e}")

    async def refresh_chat_data(self, chat_id: int, chat_data: Dict[str, Any]) -> None:
        pass

    async def drop_chat_data(self, chat_id: int) -> None:
        try:
            await self._with_retry(lambda: self.redis.hdel("ptb:chat_data", str(int(chat_id))))
        except Exception as e:
            logger.error(f"Error dropping chat_data for chat_id {chat_id}: {e}")

    # -------------------------------------------------------------------------
    # BOT DATA
    # -------------------------------------------------------------------------

    async def get_bot_data(self) -> Dict[str, Any]:
        try:
            data = await self._with_retry(lambda: self.redis.get("ptb:bot_data"))
            return self._safe_json_loads(data) or {}
        except Exception as e:
            logger.error(f"Error fetching bot_data from Redis: {e}")
            return {}

    async def update_bot_data(self, data: Dict[str, Any]) -> None:
        try:
            await self._with_retry(lambda: self.redis.set("ptb:bot_data", json.dumps(data)))
        except Exception as e:
            # Suppress noisy spam logs if DNS or connection is temporarily down during cold-start
            if "11001" in str(e) or "getaddrinfo" in str(e):
                logger.warning(f"⚠️ Redis host unreachable (DNS/Network). Skipping bot_data periodic sync.")
            else:
                logger.error(f"Error updating bot_data in Redis: {e}")

    async def refresh_bot_data(self, bot_data: Dict[str, Any]) -> None:
        pass

    # -------------------------------------------------------------------------
    # CALLBACK DATA
    # -------------------------------------------------------------------------

    async def get_callback_data(self) -> Optional[Dict[str, Any]]:
        try:
            data = await self._with_retry(lambda: self.redis.get("ptb:callback_data"))
            return self._safe_json_loads(data)
        except Exception as e:
            logger.error(f"Error fetching callback_data from Redis: {e}")
            return None

    async def update_callback_data(self, data: Dict[str, Any]) -> None:
        try:
            await self._with_retry(lambda: self.redis.set("ptb:callback_data", json.dumps(data)))
        except Exception as e:
            logger.error(f"Error updating callback_data in Redis: {e}")

    # -------------------------------------------------------------------------
    # CONVERSATIONS (Strict Per-User/Chat Isolation)
    # -------------------------------------------------------------------------

    async def get_conversations(self, name: str) -> Dict[Tuple[int, ...], Any]:
        try:
            hash_key = f"ptb:conversations:{name}"
            data = await self._with_retry(lambda: self.redis.hgetall(hash_key))
            result: Dict[Tuple[int, ...], Any] = {}
            if data:
                for k, v in data.items():
                    key_str = self._safe_decode(k)
                    val_str = self._safe_decode(v)
                    if key_str and val_str:
                        conv_key = self._deserialize_conv_key(key_str)
                        if conv_key is not None:
                            result[conv_key] = self._safe_json_loads(val_str)
            return result
        except Exception as e:
            logger.error(f"Error fetching conversation '{name}' from Redis: {e}")
            return {}

    async def update_conversation(
        self, name: str, key: Tuple[int, ...], new_state: Optional[object]
    ) -> None:
        try:
            hash_key = f"ptb:conversations:{name}"
            field_key = self._serialize_conv_key(key)

            if new_state is None:
                # User left conversation or reached END: remove state atomically
                await self._with_retry(lambda: self.redis.hdel(hash_key, field_key))
            else:
                # Write state ONLY for this specific user key tuple
                await self._with_retry(lambda: self.redis.hset(
                    hash_key,
                    mapping={field_key: json.dumps(new_state)}
                ))
        except Exception as e:
            logger.error(f"Error updating conversation '{name}' for key {key} in Redis: {e}")

    # -------------------------------------------------------------------------
    # LIFECYCLE HOOKS
    # -------------------------------------------------------------------------

    async def flush(self) -> None:
        """Inline atomic operations commit immediately; no buffering required."""
        pass
