"""Monitor Service – RTSP stream ingestion, frame extraction, and gRPC push."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import TYPE_CHECKING

import cv2
import grpc
import numpy as np
from grpc import aio as grpc_aio

if TYPE_CHECKING:
    from app.config import AppConfig, StreamConfig

logger = logging.getLogger(__name__)

# Lazy gRPC stub import
_pb2: object | None = None
_pb2_grpc: object | None = None


def _ensure_grpc_stubs():  # noqa: ANN202
    global _pb2, _pb2_grpc
    if _pb2 is None:
        from app.grpc_generated import frame_stream_pb2, frame_stream_pb2_grpc
        _pb2 = frame_stream_pb2
        _pb2_grpc = frame_stream_pb2_grpc


class StreamTask:
    """Manages a single RTSP stream: connect, extract frames, push via gRPC."""

    def __init__(
        self,
        stream_cfg: StreamConfig,
        grpc_target: str,
    ) -> None:
        self._cfg = stream_cfg
        self._grpc_target = grpc_target
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
        _ensure_grpc_stubs()
        interval = self._compute_interval()
        channel = grpc_aio.insecure_channel(self._grpc_target)
        stub = _pb2_grpc.FrameStreamServiceStub(channel)
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

                    request = _pb2.FrameRequest(
                        stream_id=self._cfg.id,
                        stream_name=self._cfg.name,
                        image_data=image_bytes,
                        timestamp_ms=ts_ms,
                        width=frame.shape[1],
                        height=frame.shape[0],
                        target_labels=self._cfg.labels,
                    )

                    try:
                        await stub.PushFrame(request)
                    except grpc.RpcError:
                        logger.exception("gRPC push failed for stream %s", self._cfg.id)

                    await asyncio.sleep(interval)
            finally:
                cap.release()

        await channel.close()


class MonitorService:
    """Orchestrates all stream tasks based on configuration."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        grpc_target = f"{config.grpc.detection_host}:{config.grpc.detection_port}"
        self.tasks: dict[str, StreamTask] = {}
        for s in config.streams:
            if s.enabled:
                self.tasks[s.id] = StreamTask(s, grpc_target)

    async def start_all(self) -> None:
        for t in self.tasks.values():
            await t.start()

    async def stop_all(self) -> None:
        for t in self.tasks.values():
            await t.stop()
