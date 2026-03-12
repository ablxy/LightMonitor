"""Alarm push service – sends detection alerts to an external webhook."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.config import AlarmConfig
    from app.models import FrameResult

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubles on each retry


class AlarmService:
    """HTTP client that POSTs alarm payloads to an external system."""

    def __init__(self, config: AlarmConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(timeout=10.0)

    # ------------------------------------------------------------------
    async def push(self, result: FrameResult, *, image_url: str = "") -> bool:
        """Push an alarm to the configured webhook URL.

        Retries up to ``_MAX_RETRIES`` times with exponential back-off.
        Returns True on success, False otherwise.
        """
        if not self._config.enabled or not self._config.webhook_url:
            return False

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._config.auth.type == "bearer" and self._config.auth.token:
            headers["Authorization"] = f"Bearer {self._config.auth.token}"
        elif self._config.auth.type == "api_key" and self._config.auth.token:
            headers["X-API-Key"] = self._config.auth.token

        payload = {
            "stream_id": result.stream_id,
            "stream_name": result.stream_name,
            "timestamp_ms": result.timestamp_ms,
            "detections": [d.model_dump() for d in result.detections],
            "image_url": image_url,
        }

        delay = _RETRY_BASE_DELAY
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = await self._client.post(
                    self._config.webhook_url,
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                logger.info("Alarm pushed for stream %s", result.stream_id)
                return True
            except httpx.HTTPError as exc:
                logger.warning(
                    "Alarm push attempt %d/%d failed for stream %s (%s)%s",
                    attempt,
                    _MAX_RETRIES,
                    result.stream_id,
                    exc,
                    f"; retrying in {delay:.1f}s…" if attempt < _MAX_RETRIES else "",
                )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(delay)
                    delay *= 2

        logger.error(
            "All %d alarm push attempts failed for stream %s",
            _MAX_RETRIES,
            result.stream_id,
        )
        return False

    async def close(self) -> None:
        await self._client.aclose()
