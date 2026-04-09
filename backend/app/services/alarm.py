"""Alarm push service – sends detection alerts to an external webhook."""

from __future__ import annotations

import asyncio
import logging

from datetime import datetime
from typing import TYPE_CHECKING

import httpx


if TYPE_CHECKING:
    from app.models import FrameResult

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubles on each retry


class AlarmService:
    """HTTP client that POSTs alarm payloads to an external system."""

    def __init__(self) -> None:
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
        target_url = report_url 
        if  not target_url:
            return False


        # Format timestamp: yyyy-MM-dd HH:mm:ss
        capture_time = datetime.fromtimestamp(result.timestamp_ms / 1000.0).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Group detections by algorithm type (using label as the type identifier)
        unique_algos = {d.label for d in result.detections}

        success = True

        for algo_type in unique_algos:
            # Filter results for this algorithm type for the 'results' array
            algo_results = []
            # Gather positions for the 'snap.data' attributes (format: [x1, y1, x2, y2, conf, label])
            snap_positions = []

            for d in result.detections:
                if d.label == algo_type and d.bbox:
                    # 1. 填充 results 数组 (x, y, w, h)
                    algo_results.append({
                        "confidence": d.confidence if d.confidence is not None else None,
                        "x": int(d.bbox.x_min),
                        "y": int(d.bbox.y_min),
                        "width": int(d.bbox.x_max - d.bbox.x_min),
                        "height": int(d.bbox.y_max - d.bbox.y_min),
                    })
                    
                    # 2. 填充 snap.data.attributes.result.positions
                    # 构建基础坐标数据
                    pos_item = [
                        int(d.bbox.x_min),
                        int(d.bbox.y_min),
                        int(d.bbox.x_max),
                        int(d.bbox.y_max)
                    ]
                    
                    # confidence 存在（不为 None），则添加格式化后的字符串
                    if d.confidence is not None:
                        pos_item.append(f"{d.confidence:.4f}")
                    
                    pos_item.append(d.label)
                    
                    snap_positions.append(pos_item)

            if not algo_results:
                continue

            # Construct snap.data structure to match the example
            snap_data_payload = {
                "attributes": {
                    "result": {
                        "num": len(snap_positions),
                        "alarm": True,
                        "positions": snap_positions
                    }
                },
                "code": 0,
                "success": True,
                "message": "success",
                "timestamp": capture_time,
                # taskID 和 timecost 如果没有对应数据，可以模拟或留空
                "taskID": f"task-{result.timestamp_ms}", 
                "timecost": "0ms" 
            }

            # Construct the final payload
            payload = {
                "algorithmType": algo_type,
                "bindId": result.stream_id,
                "cameraId": result.stream_name,
                "results": algo_results,
                "snap": {
                    "data": snap_data_payload,
                    "captureBase64": result.image_base64 or "",
                    "captureTime": capture_time,
                },
            }

            # Send the request
            if not await self._send_payload(target_url, payload, result.stream_id):
                success = False

        return success

    async def _send_payload(
        self, url: str, payload: dict, stream_id: str
    ) -> bool:
        """Helper to send a single payload with retries."""
        delay = _RETRY_BASE_DELAY
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = await self._client.post(
                    url,
                    json=payload,
                )
                # 记录详细的响应内容有助于调试
                if resp.status_code != 200:
                    logger.warning("Alarm server responded with status %s: %s", resp.status_code, resp.text)
                
                resp.raise_for_status()
                
                # 可选：检查返回体中的 resultCode
                try:
                    resp_data = resp.json()
                    if resp_data.get("resultCode") == 0:
                        logger.info("Alarm pushed successfully to %s for stream %s", url, stream_id)
                    else:
                        logger.warning("Alarm pushed but server returned error: %s", resp_data)
                except Exception:
                    logger.info("Alarm pushed to %s for stream %s (response not JSON)", url, stream_id)

                return True
            except httpx.HTTPError as exc:
                logger.warning(
                    "Alarm push attempt %d/%d failed for stream %s (%s)%s",
                    attempt,
                    _MAX_RETRIES,
                    stream_id,
                    str(exc),
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
