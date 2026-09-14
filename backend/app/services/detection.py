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
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from app.errors import AppError, ModelInferenceError, ModelResponseError
from app.models import BoundingBox, DetectionResult, FrameResult, HistoryRecord, Task
from app.observability import safe_url

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.services.alarm import AlarmService
    from app.services.database import DatabaseService

logger = logging.getLogger(__name__)

# Keep the N most recent results per stream for the API to serve.
MAX_RESULTS_PER_STREAM = 50


@dataclass(frozen=True, slots=True)
class AlarmJob:
    result: FrameResult
    image_url: str
    report_url: str


class DetectionService:
    """Consumes frames from the async queue, runs inference, stores results."""

    def __init__(
        self,
        config: AppConfig,
        alarm_service: AlarmService,
        queue: asyncio.Queue[Task],
        db_service: DatabaseService,
        num_workers: int = 4,
    ) -> None:
        self._config = config
        self._alarm = alarm_service
        self._queue = queue
        self._db = db_service
        self._num_workers = num_workers
        self._http = httpx.AsyncClient(timeout=30.0, trust_env=False)
        self._consumer_tasks: list[asyncio.Task] = []
        self._alarm_queue: asyncio.Queue[AlarmJob] = asyncio.Queue(
            maxsize=config.queue.alarm_maxsize
        )
        self._alarm_tasks: list[asyncio.Task] = []
        self._last_errors: dict[str, dict[str, str | int]] = {}
        self._inference_futures: dict[str, asyncio.Task[list[dict]]] = {}
        self._inference_cache_limit = max(config.queue.maxsize * 2, 64)
        self._expired_frames = 0
        self._last_expiry_log_at = 0.0

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
        if any(not task.done() for task in self._consumer_tasks):
            return
        self._consumer_tasks = [
            asyncio.create_task(self._consume_loop(), name=f"detect-worker-{i}")
            for i in range(self._num_workers)
        ]
        self._alarm_tasks = [
            asyncio.create_task(self._alarm_loop(), name=f"alarm-worker-{i}")
            for i in range(self._config.queue.alarm_workers)
        ]
        logger.info(
            "pipeline workers started",
            extra={
                "event": "pipeline.workers.started",
                "detection_workers": len(self._consumer_tasks),
                "alarm_workers": len(self._alarm_tasks),
            },
        )

    async def close(self) -> None:
        timeout = self._config.queue.shutdown_timeout_s
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "detection queue did not drain before shutdown",
                extra={
                    "event": "queue.detection.shutdown_timeout",
                    "queue_depth": self._queue.qsize(),
                },
            )
        if self._consumer_tasks:
            for t in self._consumer_tasks:
                t.cancel()
            await asyncio.gather(*self._consumer_tasks, return_exceptions=True)
            self._consumer_tasks.clear()
        for future in self._inference_futures.values():
            if not future.done():
                future.cancel()
        if self._inference_futures:
            await asyncio.gather(
                *self._inference_futures.values(), return_exceptions=True
            )
            self._inference_futures.clear()
        try:
            await asyncio.wait_for(self._alarm_queue.join(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "alarm queue did not drain before shutdown",
                extra={
                    "event": "queue.alarm.shutdown_timeout",
                    "queue_depth": self._alarm_queue.qsize(),
                },
            )
        for task in self._alarm_tasks:
            task.cancel()
        if self._alarm_tasks:
            await asyncio.gather(*self._alarm_tasks, return_exceptions=True)
            self._alarm_tasks.clear()
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Consumer loop
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """Continuously consume tasks from the queue and process them."""
        while True:
            task: Task = await self._queue.get()
            try:
                age_s = time.monotonic() - task.enqueued_at
                if age_s > self._config.queue.max_frame_age_s:
                    self._record_expired_frame(task, age_s)
                    continue
                await self._process_task(task)
                self._last_errors.pop(task.bindId, None)
            except AppError as exc:
                self._last_errors[task.bindId] = {
                    "last_error_code": exc.code,
                    "last_error_message": exc.message,
                    "last_error_at": int(time.time() * 1000),
                }
                logger.warning(
                    "task processing failed",
                    exc_info=True,
                    extra={
                        "event": "detection.task.failed",
                        "error_code": exc.code,
                        "task_id": task.task_id,
                        "bind_id": task.bindId,
                    },
                )
            except Exception as exc:
                self._last_errors[task.bindId] = {
                    "last_error_code": "INTERNAL_ERROR",
                    "last_error_message": "检测任务处理失败",
                    "last_error_at": int(time.time() * 1000),
                }
                logger.exception(
                    "unhandled task processing error",
                    extra={
                        "event": "detection.task.unhandled_error",
                        "error_type": type(exc).__name__,
                        "task_id": task.task_id,
                        "bind_id": task.bindId,
                    },
                )
            finally:
                self._queue.task_done()

    def _record_expired_frame(self, task: Task, age_s: float) -> None:
        self._expired_frames += 1
        now = time.monotonic()
        if self._expired_frames == 1 or now - self._last_expiry_log_at >= 10:
            logger.warning(
                "expired video frames dropped",
                extra={
                    "event": "queue.frame.expired",
                    "task_id": task.task_id,
                    "bind_id": task.bindId,
                    "age_ms": round(age_s * 1000, 2),
                    "expired_total": self._expired_frames,
                },
            )
            self._last_expiry_log_at = now

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

    async def _call_model_once(self, task: Task) -> list[dict]:
        """Share one model request between bindings for the same captured frame."""
        future = self._inference_futures.get(task.frame_id)
        if future is None:
            future = asyncio.create_task(
                self._call_model(task.image_data),
                name=f"inference-{task.frame_id}",
            )
            self._inference_futures[task.frame_id] = future
            if len(self._inference_futures) > self._inference_cache_limit:
                for frame_id, candidate in list(self._inference_futures.items()):
                    if candidate.done() and frame_id != task.frame_id:
                        self._inference_futures.pop(frame_id, None)
                        break
        return await asyncio.shield(future)

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
            if not isinstance(data, dict) or not isinstance(
                data.get("detections", []), list
            ):
                raise ModelResponseError(context={"model_type": "yolo"})
            return data.get("detections", [])
        except httpx.HTTPError as exc:
            raise ModelInferenceError(
                context={"model_type": "yolo", "error_type": type(exc).__name__}
            ) from exc
        except (ValueError, TypeError) as exc:
            raise ModelResponseError(context={"model_type": "yolo"}) from exc

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
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": vlm.prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        )

        payload: dict = {"messages": messages}
        if cfg.model_name:
            payload["model"] = cfg.model_name

        try:
            resp = await self._http.post(cfg.model_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content: str = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ModelResponseError(context={"model_type": "vlm"})
            return self._parse_vlm_response(content)
        except httpx.HTTPError as exc:
            raise ModelInferenceError(
                context={"model_type": "vlm", "error_type": type(exc).__name__}
            ) from exc
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            raise ModelResponseError(context={"model_type": "vlm"}) from exc

    async def _simulate_call_vlm_model(self, image_bytes: bytes) -> list[dict]:
        """Simulate a VLM call for testing purposes.

        Sends the frame as a base64 data-URL inside a multimodal message.
        Parses the model's text reply as JSON detections.
        """
        # Simulate a delay
        await asyncio.sleep(5)
        # Return a fake detection for testing
        return [
            {
                "label": "41814000001",
                "confidence": 0.95,
                "bbox": {"x_min": 100, "y_min": 50, "x_max": 200, "y_max": 400},
            }
        ]

    def _parse_vlm_response(self, content: str) -> list[dict]:
        """Extract a detections list from the VLM's free-text / JSON reply."""
        content = content.strip()

        # VLMs commonly wrap the JSON in a Markdown code fence.  Parse the
        # complete JSON object instead of using a non-greedy regex, which
        # stops at the first closing brace inside a detection item.
        candidates = re.findall(
            r"```(?:json)?\s*(.*?)```",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )
        candidates.append(content)
        decoder = json.JSONDecoder()

        for candidate in candidates:
            parsed_values: list[object] = []
            try:
                parsed_values.append(json.loads(candidate.strip()))
            except json.JSONDecodeError:
                # Support text before or after the JSON object.
                for index, char in enumerate(candidate):
                    if char not in "[{":
                        continue
                    try:
                        value, _ = decoder.raw_decode(candidate, index)
                    except json.JSONDecodeError:
                        continue
                    parsed_values.append(value)

            for result in parsed_values:
                if not isinstance(result, dict):
                    continue
                detections = result.get("detections")
                if not isinstance(detections, list):
                    continue
                return [
                    detection for detection in detections if isinstance(detection, dict)
                ]

        raise ModelResponseError(
            details={"response_preview": content[:200]},
            context={"model_type": "vlm"},
        )

    @staticmethod
    def _parse_bbox(raw_bbox: object) -> BoundingBox | None:
        """Convert bbox objects and ``[x1, y1, x2, y2]`` arrays."""
        if isinstance(raw_bbox, dict):
            try:
                return BoundingBox.model_validate(raw_bbox)
            except (TypeError, ValueError) as exc:
                logger.warning("Ignoring invalid bbox object: %s", exc)
                return None

        if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) >= 4:
            try:
                return BoundingBox(
                    x_min=float(raw_bbox[0]),
                    y_min=float(raw_bbox[1]),
                    x_max=float(raw_bbox[2]),
                    y_max=float(raw_bbox[3]),
                )
            except (TypeError, ValueError) as exc:
                logger.warning("Ignoring invalid bbox array: %s", exc)
                return None

        if raw_bbox:
            logger.warning("Ignoring unsupported bbox format: %r", raw_bbox)
        return None

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
        raw_dets = await self._call_model_once(task)
        duration_time = int(time.time() * 1000) - time_now
        logger.debug(
            "model inference completed",
            extra={
                "event": "model.inference.completed",
                "task_id": task.task_id,
                "bind_id": task.bindId,
                "duration_ms": duration_time,
                "raw_detection_count": len(raw_dets),
            },
        )

        threshold = (
            task.confidence_threshold
            if task.confidence_threshold is not None
            else self._config.detection.confidence_threshold
        )
        detections: list[DetectionResult] = []
        for d in raw_dets:
            if not isinstance(d, dict):
                logger.warning("Ignoring malformed detection: %r", d)
                continue

            try:
                conf = float(d.get("confidence", 0) or 0)
            except (TypeError, ValueError):
                logger.warning("Ignoring detection with invalid confidence: %r", d)
                continue

            label = str(d.get("label", "")).strip()
            if not label:
                logger.warning("Ignoring detection without a label: %r", d)
                continue
            if conf < threshold:
                continue
            bbox = self._parse_bbox(d.get("bbox"))
            detections.append(DetectionResult(label=label, confidence=conf, bbox=bbox))

        # Determine if any detection matches a target label -> alarm
        target_labels = {
            str(label).strip().casefold()
            for label in task.target_labels
            if str(label).strip()
        }
        alarmed = any(det.label.casefold() in target_labels for det in detections)
        if detections and not alarmed:
            logger.debug(
                "detections did not match target labels",
                extra={
                    "event": "detection.labels.unmatched",
                    "task_id": task.task_id,
                    "bind_id": task.bindId,
                    "model_labels": ",".join(sorted({det.label for det in detections})),
                    "target_labels": ",".join(sorted(target_labels)),
                },
            )

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
            self.results[task.bindId] = collections.deque(maxlen=MAX_RESULTS_PER_STREAM)
        result_buffer = self.results[task.bindId]
        if result_buffer:
            # Only the newest preview needs a full image in the dashboard.
            result_buffer[0].image_base64 = None
        result_buffer.appendleft(frame_result)

        logger.debug(
            "detection result stored",
            extra={
                "event": "detection.result.stored",
                "task_id": task.task_id,
                "bind_id": task.bindId,
                "detection_count": len(detections),
                "alarmed": alarmed,
            },
        )
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

            report_url = task.result_report_url or self._config.report.result_report_url
            if not report_url:
                logger.warning(
                    "alarm detected without a report endpoint",
                    extra={
                        "event": "alarm.endpoint.missing",
                        "task_id": task.task_id,
                        "bind_id": task.bindId,
                    },
                )
            else:
                job = AlarmJob(frame_result.model_copy(), image_url, report_url)
                try:
                    self._alarm_queue.put_nowait(job)
                except asyncio.QueueFull:
                    logger.error(
                        "alarm queue full; alarm dropped",
                        extra={
                            "event": "queue.alarm.full",
                            "task_id": task.task_id,
                            "bind_id": task.bindId,
                            "queue_depth": self._alarm_queue.qsize(),
                        },
                    )

    async def _alarm_loop(self) -> None:
        while True:
            job = await self._alarm_queue.get()
            try:
                await self._push_alarm(
                    job.result,
                    image_url=job.image_url,
                    report_url=job.report_url,
                )
            finally:
                self._alarm_queue.task_done()

    async def _push_alarm(
        self,
        frame_result: FrameResult,
        *,
        image_url: str,
        report_url: str,
    ) -> None:
        """Send an alarm and keep fire-and-forget failures visible in logs."""
        try:
            sent = await self._alarm.push(
                frame_result,
                image_url=image_url,
                report_url=report_url,
            )
        except Exception:
            logger.exception(
                "unhandled alarm delivery error",
                extra={
                    "event": "alarm.push.unhandled_error",
                    "stream_id": frame_result.stream_id,
                    "endpoint": safe_url(report_url),
                },
            )
            return
        if not sent:
            logger.warning(
                "alarm was not accepted",
                extra={
                    "event": "alarm.push.not_accepted",
                    "stream_id": frame_result.stream_id,
                    "endpoint": safe_url(report_url),
                },
            )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_recent_results(self, stream_id: str, limit: int = 20) -> list[FrameResult]:
        dq = self.results.get(stream_id)
        if not dq:
            return []
        return list(dq)[:limit]

    def get_last_error(self, stream_id: str) -> dict[str, str | int] | None:
        error = self._last_errors.get(stream_id)
        return dict(error) if error else None
