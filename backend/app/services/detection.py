"""Detection Service – async queue consumer that runs AI inference, uploads to
RustFS (S3), and persists results as JSONL."""

from __future__ import annotations

import asyncio
import base64
import collections
import datetime
import io
import logging
import os
import time
from typing import TYPE_CHECKING
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

import httpx

from app.models import BoundingBox, DetectionResult, FrameResult, HistoryRecord, Task

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.services.alarm import AlarmService

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
        num_workers: int = 4,
    ) -> None:
        self._config = config
        self._alarm = alarm_service
        self._queue = queue
        self._num_workers = num_workers
        self._http = httpx.AsyncClient(timeout=30.0)
        self._consumer_tasks: list[asyncio.Task] = []

        # RustFS (S3 compatible) client (initialized synchronously)
        rc = config.rustfs
        self._bucket = rc.bucket

        # Determine endpoint URL with scheme
        protocol = "https" if rc.secure else "http"
        endpoint = rc.endpoint
        if not endpoint.startswith("http"):
            endpoint = f"{protocol}://{endpoint}"

        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=rc.access_key,
            aws_secret_access_key=rc.secret_key,
            config=Config(signature_version="s3v4"),
        )

        # JSONL log path
        self._jsonl_path = config.logging.jsonl_path

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
    # AI model invocation
    # ------------------------------------------------------------------

    async def _call_model(self, image_bytes: bytes) -> list[dict]:
        """Call the external AI model API and return raw detection dicts."""
        cfg = self._config.detection
        if not cfg.model_url:
            return []

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if cfg.auth.type == "bearer" and cfg.auth.token:
            headers["Authorization"] = f"Bearer {cfg.auth.token}"
        elif cfg.auth.type == "api_key" and cfg.auth.token:
            headers["X-API-Key"] = cfg.auth.token

        b64 = base64.b64encode(image_bytes).decode()
        payload = {"image": b64}

        try:
            resp = await self._http.post(cfg.model_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            # Expect {"detections": [{label, confidence, bbox: {x_min,y_min,x_max,y_max}}]}
            return data.get("detections", [])
        except httpx.HTTPError:
            logger.exception("Model call failed")
            return []

    # ------------------------------------------------------------------
    # RustFS / S3 helpers
    # ------------------------------------------------------------------

    async def _ensure_bucket(self) -> None:
        """Create the RustFS bucket if it does not exist (thread-offloaded)."""
        def _create():
            try:
                # Try to head bucket first to check existence and permissions
                self._s3.head_bucket(Bucket=self._bucket)
            except ClientError as e:
                # If 404 Not Found, create it
                error_code = e.response.get("Error", {}).get("Code")
                if error_code == "404":
                    try:
                        self._s3.create_bucket(Bucket=self._bucket)
                        logger.info("Created RustFS bucket: %s", self._bucket)
                    except ClientError:
                        logger.exception("Failed to create RustFS bucket %s", self._bucket)
                else:
                    # Other errors (403 Forbidden etc)
                    logger.warning("Issue checking RustFS bucket %s: %s", self._bucket, e)

        await asyncio.to_thread(_create)

    async def _upload_image(self, task: Task) -> str:
        """Upload image bytes to RustFS and return a presigned URL."""
        object_name = (
            f"{task.bindId}/{task.timestamp_ms}_{task.task_id}.jpg"
        )

        def _upload():
            try:
                self._s3.put_object(
                    Bucket=self._bucket,
                    Key=object_name,
                    Body=task.image_data,
                    ContentType="image/jpeg",
                )
                # Return a presigned URL valid for 7 days
                return self._s3.generate_presigned_url(
                    ClientMethod="get_object",
                    Params={"Bucket": self._bucket, "Key": object_name},
                    ExpiresIn=int(datetime.timedelta(days=7).total_seconds()),
                )
            except ClientError:
                logger.exception(
                    "RustFS upload failed for task %s (stream %s)",
                    task.task_id,
                    task.bindId,
                )
                return ""

        return await asyncio.to_thread(_upload)

    # ------------------------------------------------------------------
    # JSONL persistence
    # ------------------------------------------------------------------

    async def _write_jsonl(self, record: HistoryRecord) -> None:
        """Append a HistoryRecord as a JSONL line (thread-offloaded)."""
        line = record.model_dump_json() + "\n"

        def _append():
            os.makedirs(os.path.dirname(self._jsonl_path) or ".", exist_ok=True)
            with open(self._jsonl_path, "a", encoding="utf-8") as fh:
                fh.write(line)

        await asyncio.to_thread(_append)

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
            # Upload image to RustFS
            await self._ensure_bucket()
            image_url = await self._upload_image(task)

            # Persist JSONL record
            record = HistoryRecord(
                task_id=task.task_id,
                timestamp_ms=task.timestamp_ms,
                stream_id=task.bindId,
                stream_name=task.cameraId,
                detections=detections,
                image_url=image_url,
            )
            await self._write_jsonl(record)

            # Trigger alarm with RustFS URL
            asyncio.create_task(
                self._alarm.push(frame_result, image_url=image_url, report_url=task.result_report_url)
            )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_recent_results(self, stream_id: str, limit: int = 20) -> list[FrameResult]:
        dq = self.results.get(stream_id)
        if not dq:
            return []
        return list(dq)[:limit]