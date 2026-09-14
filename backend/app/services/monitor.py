"""Monitor Service – RTSP stream ingestion, frame extraction, and queue push."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import TYPE_CHECKING

import cv2
import httpx

from app.errors import ExternalServiceError
from app.models import MonitorStatus, Task
from app.observability import reset_request_id, safe_url, set_request_id

if TYPE_CHECKING:
    from app.config import AppConfig, StreamConfig

logger = logging.getLogger(__name__)


class StreamBinding:
    """API-facing view of one binding backed by a shared StreamTask."""

    def __init__(self, config: StreamConfig, source: StreamTask) -> None:
        self._cfg = config
        self._source = source

    @property
    def stream_id(self) -> str:
        return self._cfg.bindId

    @property
    def stream_name(self) -> str:
        return self._cfg.cameraId

    @property
    def labels(self) -> list[str]:
        return self._cfg.labels

    @property
    def status(self) -> str:
        return self._source.status

    @property
    def latest_frame_ts(self) -> int | None:
        return self._source.latest_frame_ts


class StreamTask:
    """Manages a single RTSP stream: connect, extract frames, push to queue."""

    def __init__(
        self,
        stream_cfg: StreamConfig,
        queue: asyncio.Queue[Task],
    ) -> None:
        self._cfg = stream_cfg
        self._bindings: dict[str, StreamConfig] = {stream_cfg.bindId: stream_cfg}
        self._next_frame_at: dict[str, float] = {stream_cfg.bindId: 0.0}
        self._dispatch_offset = 0
        self._dropped_frames = 0
        self._last_drop_log_at = 0.0
        self._queue = queue
        self._task: asyncio.Task | None = None
        self._status: MonitorStatus = MonitorStatus.INIT
        self.latest_frame_ts: int | None = None

    @property
    def stream_id(self) -> str:
        return self._cfg.bindId

    @property
    def stream_name(self) -> str:
        return self._cfg.cameraId

    @property
    def labels(self) -> list[str]:
        return self._cfg.labels

    @property
    def status(self) -> str:
        """Return the public, JSON-friendly task status for API consumers."""
        return self._status.name.lower()

    def _compute_interval(self) -> float:
        """Return the sleep interval in seconds between frame extractions."""
        return min(self._interval_for(cfg) for cfg in self._bindings.values())

    @staticmethod
    def _interval_for(cfg: StreamConfig) -> float:
        fe = cfg.frame_extraction
        if fe.fps and fe.fps > 0:
            return 1.0 / fe.fps
        if fe.interval_s and fe.interval_s > 0:
            return fe.interval_s
        return 1.0  # default: 1 frame/sec

    def add_binding(self, config: StreamConfig) -> StreamBinding:
        self._bindings[config.bindId] = config
        self._next_frame_at[config.bindId] = 0.0
        return StreamBinding(config, self)

    def update_binding(self, config: StreamConfig) -> StreamBinding:
        self._bindings[config.bindId] = config
        self._next_frame_at[config.bindId] = 0.0
        if self._cfg.bindId == config.bindId:
            self._cfg = config
        return StreamBinding(config, self)

    async def remove_binding(self, bind_id: str) -> bool:
        config = self._bindings.pop(bind_id, None)
        self._next_frame_at.pop(bind_id, None)
        if config is None:
            return False
        await self._upload_config_status(config, MonitorStatus.STOP)
        if self._bindings and self._cfg.bindId == bind_id:
            self._cfg = next(iter(self._bindings.values()))
        return True

    @property
    def binding_count(self) -> int:
        return len(self._bindings)

    def _record_dropped_frame(self, bind_id: str, reason: str) -> None:
        self._dropped_frames += 1
        now = time.monotonic()
        if self._dropped_frames == 1 or now - self._last_drop_log_at >= 10:
            logger.warning(
                "video frames dropped due to backpressure",
                extra={
                    "event": "queue.frame.dropped",
                    "bind_id": bind_id,
                    "reason": reason,
                    "queue_depth": self._queue.qsize(),
                    "dropped_total": self._dropped_frames,
                },
            )
            self._last_drop_log_at = now

    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._task and not self._task.done():
            logger.info(
                "Stream task %s is already active with status %s",
                self.stream_id,
                self._status.name,
            )
            return
        self._status = MonitorStatus.STARTING

        try:
            logger.info(
                "stream task starting",
                extra={"event": "stream.task.starting", "bind_id": self.stream_id},
            )

            context_token = set_request_id("-")
            try:
                self._task = asyncio.create_task(
                    self._run_loop(), name=f"stream-source-{self._cfg.cameraId}"
                )
            finally:
                reset_request_id(context_token)

            await self.upload_status()
        except Exception as e:
            self._status = MonitorStatus.ERROR
            logger.exception(
                "stream task failed to start",
                extra={
                    "event": "stream.task.start_failed",
                    "bind_id": self.stream_id,
                    "error_type": type(e).__name__,
                },
            )
            await self.upload_status()
            raise

    async def update_config(self, new_cfg: StreamConfig) -> None:
        """Update stream configuration dynamically (restart if needed)."""
        logger.info("Updating stream config for %s", self.stream_id)
        need_restart = self._cfg.live_url != new_cfg.live_url
        self._cfg = new_cfg

        if self._status not in (MonitorStatus.RUNNING, MonitorStatus.STARTING):
            # 非运行状态，直接启动
            await self.start()
        elif need_restart:
            # 运行中但 URL 变了，重启
            await self.stop()
            await self.start()

    async def stop(self) -> None:
        self._status = MonitorStatus.STOP
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._status = MonitorStatus.STOP

        await self.upload_status()

    async def get_video_streaming(self) -> str:
        """获取摄像头实时视频流"""
        camera_id = self._cfg.cameraId
        get_video_streaming_url = self._cfg.live_url
        payload = {"cameraId": camera_id}

        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            try:
                response = await client.request(
                    "POST", get_video_streaming_url, json=payload
                )
                response.raise_for_status()
                response_data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ExternalServiceError(
                    "获取视频流地址失败",
                    context={
                        "camera_id": camera_id,
                        "endpoint": safe_url(get_video_streaming_url),
                        "error_type": type(exc).__name__,
                    },
                ) from exc

        result_code = response_data.get("resultCode")
        video_url = response_data.get("url")
        if result_code != 0 or not video_url:
            raise ExternalServiceError(
                "视频流地址服务返回失败",
                details={"result_code": result_code},
                context={"camera_id": camera_id},
            )
        return str(video_url)

    async def upload_status(self) -> None:
        await asyncio.gather(
            *(
                self._upload_config_status(config, self._status)
                for config in self._bindings.values()
            ),
            return_exceptions=True,
        )

    async def _upload_config_status(
        self, config: StreamConfig, status: MonitorStatus
    ) -> None:
        url = config.report.status_report_url
        if not url:
            return
        payload = {
            "algorithmType": config.labels[0] if config.labels else "",
            "bindId": config.bindId,
            "cameraId": config.cameraId,
            "status": status.value,
        }
        from app.config import get_config

        cfg = get_config()
        cred = f"{cfg.api_auth.username}:{cfg.api_auth.password}"
        basic_token = base64.b64encode(cred.encode("utf-8")).decode("ascii")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {basic_token}",
        }
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                logger.info(
                    "status report sent",
                    extra={
                        "event": "status.report.sent",
                        "bind_id": config.bindId,
                        "endpoint": safe_url(url),
                    },
                )
            except httpx.HTTPError as e:
                logger.warning(
                    "status report failed",
                    extra={
                        "event": "status.report.failed",
                        "bind_id": config.bindId,
                        "endpoint": safe_url(url),
                        "error_type": type(e).__name__,
                    },
                )

    async def _run_loop(self) -> None:
        reconnect_delay = 2.0
        max_reconnect_delay = 30.0
        retry_count = 0
        max_retries = 3

        while self._status in (
            MonitorStatus.STARTING,
            MonitorStatus.RUNNING,
            MonitorStatus.ERROR,
        ):
            try:
                if self._cfg.live_url.lower().startswith(("rtsp://", "rtsps://")):
                    rtsp_url = self._cfg.live_url
                else:
                    rtsp_url = await self.get_video_streaming()
                logger.info(
                    "opening RTSP stream",
                    extra={
                        "event": "stream.opening",
                        "bind_id": self._cfg.bindId,
                        "endpoint": safe_url(rtsp_url),
                    },
                )
                cap = await asyncio.to_thread(cv2.VideoCapture, rtsp_url)
            except Exception as e:
                self._status = MonitorStatus.ERROR
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(
                        "Stream %s failed after %d retries, giving up.",
                        self._cfg.bindId,
                        max_retries,
                    )
                    await self.upload_status()
                    return
                logger.warning(
                    "RTSP stream preparation failed",
                    exc_info=True,
                    extra={
                        "event": "stream.open.failed",
                        "source_id": self._cfg.cameraId,
                        "attempt": retry_count,
                        "max_attempts": max_retries,
                        "retry_delay_s": reconnect_delay,
                        "error_type": type(e).__name__,
                    },
                )
                await self.upload_status()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                continue

            if not cap.isOpened():
                self._status = MonitorStatus.ERROR
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(
                        "Stream %s failed after %d retries, giving up.",
                        self._cfg.bindId,
                        max_retries,
                    )
                    cap.release()
                    await self.upload_status()
                    return
                logger.warning(
                    "RTSP stream could not be opened",
                    extra={
                        "event": "stream.open.rejected",
                        "source_id": self._cfg.cameraId,
                        "endpoint": safe_url(rtsp_url),
                        "attempt": retry_count,
                        "max_attempts": max_retries,
                        "retry_delay_s": reconnect_delay,
                    },
                )
                cap.release()
                await self.upload_status()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                continue

            reconnect_delay = 2.0
            retry_count = 0
            self._status = MonitorStatus.RUNNING
            await self.upload_status()
            logger.info(
                "RTSP stream connected",
                extra={
                    "event": "stream.connected",
                    "source_id": self._cfg.cameraId,
                    "endpoint": safe_url(rtsp_url),
                    "binding_count": self.binding_count,
                },
            )

            try:
                while self._status == MonitorStatus.RUNNING:
                    ret, frame = await asyncio.to_thread(cap.read)
                    if not ret:
                        logger.warning(
                            "RTSP stream lost",
                            extra={
                                "event": "stream.connection.lost",
                                "source_id": self._cfg.cameraId,
                            },
                        )
                        self._status = MonitorStatus.ERROR
                        retry_count += 1
                        await self.upload_status()
                        break

                    # JPEG encode
                    ok, buf = cv2.imencode(".jpg", frame)
                    if not ok:
                        continue
                    image_bytes = buf.tobytes()

                    # Live-stream positions are not wall-clock timestamps.
                    ts_ms = int(time.time() * 1000)

                    self.latest_frame_ts = ts_ms

                    now = time.monotonic()
                    frame_id = str(uuid.uuid4())
                    configs = list(self._bindings.values())
                    if configs:
                        offset = self._dispatch_offset % len(configs)
                        configs = configs[offset:] + configs[:offset]
                        self._dispatch_offset += 1
                    for config in configs:
                        if now < self._next_frame_at.get(config.bindId, 0.0):
                            continue
                        self._next_frame_at[config.bindId] = now + self._interval_for(
                            config
                        )
                        task = Task(
                            frame_id=frame_id,
                            bindId=config.bindId,
                            cameraId=config.cameraId,
                            image_data=image_bytes,
                            timestamp_ms=ts_ms,
                            target_labels=config.labels,
                            confidence_threshold=config.confidence_threshold,
                            status_report_url=config.report.status_report_url,
                            result_report_url=config.report.result_report_url,
                        )
                        if self._queue.full():
                            try:
                                dropped = self._queue.get_nowait()
                                self._queue.task_done()
                                self._record_dropped_frame(
                                    dropped.bindId, "queue_full_drop_oldest"
                                )
                            except asyncio.QueueEmpty:
                                pass
                        try:
                            self._queue.put_nowait(task)
                        except asyncio.QueueFull:
                            self._record_dropped_frame(
                                config.bindId, "queue_still_full"
                            )

                    await asyncio.sleep(self._compute_interval())
            finally:
                cap.release()


class MonitorService:
    """Orchestrates all stream tasks based on configuration."""

    def __init__(self, config: AppConfig, queue: asyncio.Queue[Task]) -> None:
        self._config = config
        self._queue = queue
        self._sources: dict[str, StreamTask] = {}
        self.tasks: dict[str, StreamBinding] = {}
        self._started = False
        for stream_config in config.streams:
            if stream_config.enabled:
                self._register_binding(stream_config)

    def _register_binding(self, stream_cfg: StreamConfig) -> StreamBinding:
        source = self._sources.get(stream_cfg.cameraId)
        if source is None:
            source = StreamTask(stream_cfg, self._queue)
            self._sources[stream_cfg.cameraId] = source
            binding = StreamBinding(stream_cfg, source)
        else:
            if source._cfg.live_url != stream_cfg.live_url:
                raise ValueError("同一 cameraId 不能配置不同的视频源地址")
            binding = source.add_binding(stream_cfg)
        self.tasks[stream_cfg.bindId] = binding
        return binding

    async def start_all(self) -> None:
        self._started = True
        await asyncio.gather(*(source.start() for source in self._sources.values()))

    async def stop_all(self) -> None:
        self._started = False
        await asyncio.gather(
            *(source.stop() for source in self._sources.values()),
            return_exceptions=True,
        )

    async def init_single_stream(self, stream_cfg: StreamConfig) -> None:
        if not stream_cfg.enabled:
            return
        existing = self.tasks.get(stream_cfg.bindId)
        if existing is not None:
            if existing.stream_name != stream_cfg.cameraId:
                await self.remove_single_stream(stream_cfg.bindId)
            else:
                source = self._sources[stream_cfg.cameraId]
                if source._cfg.live_url != stream_cfg.live_url:
                    raise ValueError("更新绑定时不能更换共享视频源地址")
                self.tasks[stream_cfg.bindId] = source.update_binding(stream_cfg)
                if self._started:
                    await source.start()
                return

        binding = self._register_binding(stream_cfg)
        source = self._sources[stream_cfg.cameraId]
        if self._started:
            await source.start()
        logger.info(
            "algorithm binding registered",
            extra={
                "event": "stream.binding.registered",
                "bind_id": binding.stream_id,
                "source_id": binding.stream_name,
                "binding_count": source.binding_count,
            },
        )

    async def remove_single_stream(self, bind_id: str) -> bool:
        """Remove a binding and stop its source after the final subscriber."""
        binding = self.tasks.pop(bind_id, None)
        if binding is None:
            return False
        source = self._sources[binding.stream_name]
        if source.binding_count == 1:
            await source.stop()
            source._bindings.clear()
            source._next_frame_at.clear()
            self._sources.pop(binding.stream_name, None)
        else:
            await source.remove_binding(bind_id)
        return True
