"""Alarm push service – sends detection alerts to an external webhook."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

from app.api.v1.algo_auth import generate_md5_signature

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
    async def push(
        self,
        result: FrameResult,
        *,
        image_url: str = "",
        report_url: str = ""
    ) -> bool:
        """Push an alarm to the configured webhook URL.

        Retries up to ``_MAX_RETRIES`` times with exponential back-off.
        Returns True on success, False otherwise.
        """
        # Determine the target URL: prefer the dynamic report_url from the task,
        # otherwise fall back to the global webhook_url from config.
        target_url = report_url or self._config.webhook_url
        if not self._config.enabled or not target_url:
            return False

        request_path = "/api/sapa/report/data"
        full_url = f"http://testserver{request_path}"
        
        client_sign = generate_md5_signature(full_url)

        headers = {
            "X-Sign": client_sign  
        }

        # Format timestamp: yyyy-MM-dd HH:mm:ss
        capture_time = datetime.fromtimestamp(result.timestamp_ms / 1000.0).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Group detections by algorithm type (using label as the type identifier)
        # Assuming detections.label contains the algorithmType code (e.g. "41811000007")
        # or that we report each label type separately.
        unique_algos = {d.label for d in result.detections}

        success = True

        for algo_type in unique_algos:
            # Filter results for this algorithm type
            algo_results = []
            for d in result.detections:
                if d.label == algo_type and d.bbox:
                    algo_results.append({
                        "confidence": d.confidence,
                        "x": int(d.bbox.x_min),
                        "y": int(d.bbox.y_min),
                        "width": int(d.bbox.x_max - d.bbox.x_min),
                        "height": int(d.bbox.y_max - d.bbox.y_min),
                    })

            if not algo_results:
                continue

            # Construct the payload according to 3.2.5 spec
            payload = {
                "algorithmType": algo_type,
                "bindId": result.stream_id,
                "cameraId": result.stream_name,
                "results": algo_results,
                "snap": {
                    "data": {},  # Custom display, content omitted as requested
                    "captureBase64": result.image_base64 or "",
                    "captureTime": capture_time,
                },
            }

            # Send the request
            if not await self._send_payload(target_url, payload, headers, result.stream_id):
                success = False

        return success

    async def _send_payload(
        self, url: str, payload: dict, headers: dict, stream_id: str
    ) -> bool:
        """Helper to send a single payload with retries."""
        delay = _RETRY_BASE_DELAY
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = await self._client.post(
                    url,
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                logger.info("Alarm pushed to %s for stream %s", url, stream_id)
                return True
            except httpx.HTTPError as exc:
                logger.warning(
                    "Alarm push attempt %d/%d failed for stream %s (%s)%s",
                    attempt,
                    _MAX_RETRIES,
                    stream_id,
                    exc,
                    f"; retrying in {delay:.1f}s…" if attempt < _MAX_RETRIES else "",
                )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(delay)
                    delay *= 2

        logger.error(
            "All %d alarm push attempts failed for stream %s",
            _MAX_RETRIES,
            stream_id,
        )
        return False

    async def close(self) -> None:
        await self._client.aclose()
