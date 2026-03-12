"""Monitor Service – RTSP stream ingestion, frame extraction, and queue push."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import cv2

from app.models import Task

if TYPE_CHECKING:
    from app.config import AppConfig, StreamConfig

logger = logging.getLogger(__name__)


class StreamTask:
    """Manages a single RTSP stream: connect, extract frames, push to queue."""

    def __init__(
        self,
        stream_cfg: StreamConfig,
        queue: asyncio.Queue,
    ) -> None:
        self._cfg = stream_cfg
        self._queue = queue
        self._running = False
        self._task: asyncio.Task | None = None
        self.status: str = "offline"  # "running", "error", "offline"
        self.latest_frame_ts: int | None = None

    @property
    def stream_id(self) -> str:
        return self._cfg.id

    @property
    def stream_name(self) -> str:
        return self._cfg.name

    @property
    def labels(self) -> list[str]:
        return self._cfg.labels

    def _compute_interval(self) -> float:
        """Return the sleep interval in seconds between frame extractions."""
        fe = self._cfg.frame_extraction
        if fe.fps and fe.fps > 0:
            return 1.0 / fe.fps
        if fe.interval_s and fe.interval_s > 0:
            return fe.interval_s
        return 1.0  # default: 1 frame/sec

    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.status = "offline"

    async def _run_loop(self) -> None:
        interval = self._compute_interval()
        reconnect_delay = 2.0
        max_reconnect_delay = 30.0

        while self._running:
            cap = cv2.VideoCapture(self._cfg.rtsp_url)
            if not cap.isOpened():
                self.status = "error"
                logger.warning(
                    "Cannot open RTSP stream %s (%s), retrying in %.0fs…",
                    self._cfg.id, self._cfg.rtsp_url, reconnect_delay,
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                continue

            reconnect_delay = 2.0
            self.status = "running"
            logger.info("Connected to RTSP stream %s (%s)", self._cfg.id, self._cfg.rtsp_url)

            try:
                while self._running:
                    ret, frame = await asyncio.to_thread(cap.read)
                    if not ret:
                        logger.warning("Lost RTSP stream %s, reconnecting…", self._cfg.id)
                        self.status = "error"
                        break

                    # JPEG encode
                    ok, buf = cv2.imencode(".jpg", frame)
                    if not ok:
                        continue
                    image_bytes = buf.tobytes()
                    ts_ms = int(time.time() * 1000)
                    self.latest_frame_ts = ts_ms

                    task = Task(
                        stream_id=self._cfg.id,
                        stream_name=self._cfg.name,
                        image_data=image_bytes,
                        timestamp_ms=ts_ms,
                        target_labels=self._cfg.labels,
                    )

                    # Backpressure: drop the oldest queued frame when the queue
                    # is full so that the monitor stays real-time.
                    if self._queue.full():
                        try:
                            self._queue.get_nowait()
                            logger.warning(
                                "Queue full – dropped oldest frame for stream %s",
                                self._cfg.id,
                            )
                        except asyncio.QueueEmpty:
                            pass

                    try:
                        self._queue.put_nowait(task)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Queue still full – dropping current frame for stream %s",
                            self._cfg.id,
                        )

                    await asyncio.sleep(interval)
            finally:
                cap.release()


class MonitorService:
    """Orchestrates all stream tasks based on configuration."""

    def __init__(self, config: AppConfig, queue: asyncio.Queue) -> None:
        self._config = config
        self._queue = queue
        self.tasks: dict[str, StreamTask] = {}
        for s in config.streams:
            if s.enabled:
                self.tasks[s.id] = StreamTask(s, queue)

    async def start_all(self) -> None:
        for t in self.tasks.values():
            await t.start()

    async def stop_all(self) -> None:
        for t in self.tasks.values():
            await t.stop()
