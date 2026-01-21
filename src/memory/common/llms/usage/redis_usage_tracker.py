"""Redis-backed usage tracker implementation."""

import json
from typing import Any, Iterable, Protocol

import redis

from memory.common import settings
from memory.common.llms.usage.usage_tracker import (
    RateLimitConfig,
    UsageState,
    UsageTracker,
)


class RedisClientProtocol(Protocol):
    def get(self, name: str) -> Any:  # pragma: no cover - Protocol definition
        ...

    def set(  # pragma: no cover - Protocol definition
        self,
        name: str,
        value: Any,
        ex: Any = None,
        px: Any = None,
        nx: bool = False,
        xx: bool = False,
        keepttl: bool = False,
        get: bool = False,
        exat: Any = None,
        pxat: Any = None,
    ) -> Any:
        ...

    def scan_iter(
        self, match: str
    ) -> Iterable[Any]:  # pragma: no cover - Protocol definition
        ...


class RedisUsageTracker(UsageTracker):
    """Tracks LLM usage for providers and models using Redis for persistence."""

    _redis: RedisClientProtocol  # Always set in __init__

    def __init__(
        self,
        configs: dict[str, RateLimitConfig] | None = None,
        default_config: RateLimitConfig | None = None,
        *,
        redis_client: RedisClientProtocol | None = None,
        key_prefix: str | None = None,
    ) -> None:
        super().__init__(configs=configs, default_config=default_config)
        if redis_client is None:
            self._redis = redis.Redis.from_url(settings.REDIS_URL)
        else:
            self._redis = redis_client
        prefix = key_prefix or settings.LLM_USAGE_REDIS_PREFIX
        self._key_prefix = prefix.rstrip(":")

    def get_state(self, key: str) -> UsageState:
        redis_key = self._format_key(key)
        payload = self._redis.get(redis_key)
        if not payload:
            return UsageState()
        if isinstance(payload, bytes):
            payload = payload.decode()
        return UsageState.from_payload(json.loads(payload))

    def iter_state_items(self) -> Iterable[tuple[str, UsageState]]:
        pattern = f"{self._key_prefix}:*"
        for redis_key in self._redis.scan_iter(match=pattern):
            state_key = self._ensure_str(redis_key)
            payload = self._redis.get(state_key)
            if not payload:
                continue
            if isinstance(payload, bytes):
                payload = payload.decode()
            state = UsageState.from_payload(json.loads(payload))
            yield state_key[len(self._key_prefix) + 1 :], state

    def save_state(self, key: str, state: UsageState) -> None:
        redis_key = self._format_key(key)
        self._redis.set(
            redis_key, json.dumps(state.to_payload(), separators=(",", ":"))
        )

    def _format_key(self, key: str) -> str:
        return f"{self._key_prefix}:{key}"

    @staticmethod
    def _ensure_str(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode()
        return str(value)
