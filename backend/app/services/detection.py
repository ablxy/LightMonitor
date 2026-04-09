"""Detection Service – async queue consumer that runs AI inference, stores
snapshots on the local filesystem, and persists results to SQLite."""

from __future__ import annotations

import asyncio
import base64
import collections
import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING

import httpx

from app.models import BoundingBox, DetectionResult, FrameResult, HistoryRecord, Task

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.services.alarm import AlarmService
    from app.services.database import DatabaseService

logger = logging.getLogger(__name__)

# Keep the N most recent results per stream for the API to serve.
MAX_RESULTS_PER_STREAM = 50


class DetectionService:
    """Consumes frames from the async queue, runs inference, stores results."""

    def __init__(
        self,
        config: AppConfig,
        alarm_service: AlarmService,
        queue: asyncio.Queue,
        db_service: DatabaseService,
        num_workers: int = 4,
    ) -> None:
        self._config = config
        self._alarm = alarm_service
        self._queue = queue
        self._db = db_service
        self._num_workers = num_workers
        self._http = httpx.AsyncClient(timeout=30.0)
        self._consumer_tasks: list[asyncio.Task] = []

        # Local snapshot storage directory
        self._snapshots_dir = config.storage.snapshots_dir

        # stream_id -> deque of FrameResult
        self.results: dict[str, collections.deque[FrameResult]] = {}
        for s in config.streams:
            self.results[s.bindId] = collections.deque(maxlen=MAX_RESULTS_PER_STREAM)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background queue consumer tasks."""
        self._consumer_tasks = [
            asyncio.create_task(self._consume_loop(), name=f"detect-worker-{i}")
            for i in range(self._num_workers)
        ]
        logger.info("Started %d detection workers", len(self._consumer_tasks))

    async def close(self) -> None:
        if self._consumer_tasks:
            for t in self._consumer_tasks:
                t.cancel()
            await asyncio.gather(*self._consumer_tasks, return_exceptions=True)
            self._consumer_tasks.clear()
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Consumer loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Continuously consume tasks from the queue and process them."""
        while True:
            task: Task = await self._queue.get()
            try:
                await self._process_task(task)
            except Exception:
                logger.exception(
                    "Unhandled error processing task %s for stream %s",
                    task.task_id,
                    task.bindId,
                )
            finally:
                self._queue.task_done()

    # ------------------------------------------------------------------
    # Auth / header helpers
    # ------------------------------------------------------------------

    def _build_auth_headers(self) -> dict[str, str]:
        """Return HTTP headers with auth injected (case-insensitive type match)."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        auth = self._config.detection.auth
        auth_type = auth.type.lower()
        if auth_type == "bearer" and auth.token:
            headers["Authorization"] = f"Bearer {auth.token}"
        elif auth_type in ("api_key", "accesskeyid") and auth.token:
            headers["X-API-Key"] = auth.token
        return headers

    # ------------------------------------------------------------------
    # AI model invocation
    # ------------------------------------------------------------------

    async def _call_model(self, image_bytes: bytes) -> list[dict]:
        """Dispatch to the appropriate model backend."""
        cfg = self._config.detection
        if not cfg.model_url:
            return []
        if cfg.model_type.lower() == "vlm":
            return await self._call_vlm_model(image_bytes)
        return await self._call_yolo_model(image_bytes)

    async def _call_yolo_model(self, image_bytes: bytes) -> list[dict]:
        """Call a traditional object-detection model (YOLO-style) API.

        Expects response: {"detections": [{label, confidence, bbox: {x_min,y_min,x_max,y_max}}]}
        """
        cfg = self._config.detection
        headers = self._build_auth_headers()
        b64 = base64.b64encode(image_bytes).decode()
        payload = {"image": b64}
        try:
            resp = await self._http.post(cfg.model_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("detections", [])
        except httpx.HTTPError:
            logger.exception("YOLO model call failed")
            return []

    async def _call_vlm_model(self, image_bytes: bytes) -> list[dict]:
        """Call a VLM via OpenAI-compatible chat/completions API.

        Sends the frame as a base64 data-URL inside a multimodal message.
        Parses the model's text reply as JSON detections.
        """
        cfg = self._config.detection
        vlm = cfg.vlm
        headers = self._build_auth_headers()

        b64 = base64.b64encode(image_bytes).decode()
        messages: list[dict] = []
        if vlm.system_prompt:
            messages.append({"role": "system", "content": vlm.system_prompt})
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": vlm.prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
            ],
        })

        payload: dict = {"messages": messages}
        if cfg.model_name:
            payload["model"] = cfg.model_name

        try:
            resp = await self._http.post(cfg.model_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content: str = data["choices"][0]["message"]["content"]
            return self._parse_vlm_response(content)
        except (httpx.HTTPError, KeyError, IndexError):
            logger.exception("VLM model call failed")
            return []

    def _parse_vlm_response(self, content: str) -> list[dict]:
        """Extract a detections list from the VLM's free-text / JSON reply."""
        content = content.strip()
        # 1. Try direct JSON parse
        try:
            result = json.loads(content)
            return result.get("detections", [])
        except json.JSONDecodeError:
            pass
        # 2. Find the first JSON object that contains a 'detections' key
        match = re.search(r'\{.*?"detections".*?\}', content, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                return result.get("detections", [])
            except json.JSONDecodeError:
                pass
        logger.warning("Failed to parse VLM response as JSON: %.200s", content)
        return []

    # ------------------------------------------------------------------
    # Snapshot storage (local filesystem)
    # ------------------------------------------------------------------

    async def _save_snapshot(self, task: Task) -> str:
        """Save the frame image to disk and return an API-accessible URL path."""
        filename = f"{task.timestamp_ms}_{task.task_id}.jpg"
        stream_dir = os.path.join(self._snapshots_dir, task.bindId)

        def _write() -> str:
            os.makedirs(stream_dir, exist_ok=True)
            filepath = os.path.join(stream_dir, filename)
            with open(filepath, "wb") as fh:
                fh.write(task.image_data)
            return f"/api/v1/snapshots/{task.bindId}/{filename}"

        return await asyncio.to_thread(_write)

    # ------------------------------------------------------------------
    # SQLite persistence
    # ------------------------------------------------------------------

    async def _write_record(self, record: HistoryRecord) -> None:
        """Persist a HistoryRecord to the SQLite database."""
        await self._db.write_record(record)

    # ------------------------------------------------------------------
    # Frame processing pipeline
    # ------------------------------------------------------------------

    async def _process_task(self, task: Task) -> None:
        """Run inference; on a hit, upload to RustFS and persist JSONL."""
        time_now = int(time.time() * 1000)
        raw_dets = await self._call_model(task.image_data)
        duration_time = int(time.time() * 1000) - time_now
        logger.info("Model inference time for task %s: %d ms", task.task_id, duration_time)

        threshold = self._config.detection.confidence_threshold
        detections: list[DetectionResult] = []
        for d in raw_dets:
            conf = float(d.get("confidence", 0))
            label = d.get("label", "")
            if conf < threshold:
                continue
            bbox = None
            if "bbox" in d and d["bbox"]:
                bbox = BoundingBox(**d["bbox"])
            detections.append(DetectionResult(label=label, confidence=conf, bbox=bbox))

        # Determine if any detection matches a target label -> alarm
        alarmed = any(det.label in task.target_labels for det in detections)

        b64_image = base64.b64encode(task.image_data).decode()
        frame_result = FrameResult(
            stream_id=task.bindId,
            stream_name=task.cameraId,
            timestamp_ms=task.timestamp_ms,
            detections=detections,
            alarmed=alarmed,
            image_base64=b64_image,
        )

        # Store in-memory ring buffer
        if task.bindId not in self.results:
            self.results[task.bindId] = collections.deque(
                maxlen=MAX_RESULTS_PER_STREAM
            )
        self.results[task.bindId].appendleft(frame_result)

        if alarmed:
            # Save snapshot to local filesystem
            image_url = await self._save_snapshot(task)

            # Persist to SQLite
            record = HistoryRecord(
                task_id=task.task_id,
                timestamp_ms=task.timestamp_ms,
                stream_id=task.bindId,
                stream_name=task.cameraId,
                detections=detections,
                image_url=image_url,
            )
            await self._write_record(record)

            # Trigger alarm with RustFS URL
            asyncio.create_task(
                self._alarm.push(frame_result, image_url=image_url, report_url=task.result_report_url or "")
            )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_recent_results(self, stream_id: str, limit: int = 20) -> list[FrameResult]:
        dq = self.results.get(stream_id)
        if not dq:
            return []
        return list(dq)[:limit]