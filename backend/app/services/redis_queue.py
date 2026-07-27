"""Small Redis queue abstraction used by the future Producer/Consumer/Saver workers.

The current application can continue using its in-process queue. This module
provides the durable transport without forcing Redis to be available during
unit tests or application import.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class QueueUnavailableError(RuntimeError):
    """Raised when the Redis client dependency or connection is unavailable."""


@dataclass(frozen=True)
class QueueMessage:
    event_id: str
    payload: dict[str, Any]
    schema_version: int = 1
    attempt: int = 0

    def encode(self) -> str:
        return json.dumps(
            {
                "event_id": self.event_id,
                "schema_version": self.schema_version,
                "attempt": self.attempt,
                "payload": self.payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def decode(cls, value: str | bytes) -> "QueueMessage":
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        data = json.loads(value)
        if not isinstance(data, dict) or not data.get("event_id"):
            raise ValueError("invalid queue message: event_id is required")
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("invalid queue message: payload must be an object")
        return cls(
            event_id=str(data["event_id"]),
            payload=payload,
            schema_version=int(data.get("schema_version", 1)),
            attempt=int(data.get("attempt", 0)),
        )


class RedisQueue:
    """Reliable list-based queue using a processing list and a dead-letter list.

    ``redis`` is imported only when an instance is created, keeping the legacy
    SQLite-only test suite importable until the new dependencies are installed.
    """

    def __init__(self, url: str, name: str, *, max_retries: int = 3, client: Any = None):
        self.url = url
        self.name = name
        self.processing_name = f"{name}:processing"
        self.dead_letter_name = f"{name}:dead"
        self.max_retries = max_retries
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import redis  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - depends on environment
                raise QueueUnavailableError("redis package is not installed") from exc
            self._client = redis.Redis.from_url(self.url, decode_responses=False)
        return self._client

    def enqueue(self, message: QueueMessage) -> int:
        return int(self.client.rpush(self.name, message.encode()))

    def reserve(self, timeout: int = 1) -> QueueMessage | None:
        item = self.client.brpoplpush(self.name, self.processing_name, timeout=timeout)
        if item is None:
            return None
        return QueueMessage.decode(item)

    def acknowledge(self, message: QueueMessage) -> int:
        return int(self.client.lrem(self.processing_name, 1, message.encode()))

    def reject(self, message: QueueMessage) -> int:
        self.acknowledge(message)
        if message.attempt >= self.max_retries:
            return int(self.client.rpush(self.dead_letter_name, message.encode()))
        retry = QueueMessage(
            event_id=message.event_id,
            payload=message.payload,
            schema_version=message.schema_version,
            attempt=message.attempt + 1,
        )
        return int(self.client.rpush(self.name, retry.encode()))