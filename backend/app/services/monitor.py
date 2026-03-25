"""Monitor Service – RTSP stream ingestion, frame extraction, and queue push."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING
import httpx
import cv2

from app.models import MonitorStatus, Task 



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
        if self._status == MonitorStatus.RUNNING:
            return
        self._status = MonitorStatus.STARTING

        # 获取实时视频流URL（如果API调用失败则抛出异常，外层会捕获并设置状态为error）
        try:
            rtsp_url = await self.get_video_streaming()
            logging.info("Starting stream task %s with RTSP URL: %s", self.stream_id, rtsp_url)

            self._task = asyncio.create_task(self._run_loop(rtsp_url))

            await self.upload_status()
        except Exception as e:
            self._status = MonitorStatus.ERROR
            logger.error("Failed to start stream task %s: %s", self.stream_id, str(e))
            await self.upload_status()

    async def update_config(self, new_cfg: StreamConfig) -> None:
        """Update stream configuration dynamically (restart if needed)."""
        logger.info("Updating stream config for %s", self.stream_id)
        need_restart = (self._cfg.live_url != new_cfg.live_url)
        self._cfg = new_cfg
        
        if need_restart and self._status == MonitorStatus.RUNNING:
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

        async with httpx.AsyncClient() as client:
            try:
                response = await client.request("GET", get_video_streaming_url, json=payload)
                response.raise_for_status()
                result_code = response.json().get("resultCode")
                result_desc = response.json().get("resultDesc","Unknown error")
                if result_code ==0:
                    video_url = response.json().get("url")
                    if video_url:
                        logger.info("Camera %s: successfully fetched stream URL.", camera_id)
                        return video_url
                    else:
                        logger.error("Camera %s: API returned success but 'url' field is missing.", camera_id)
                        raise Exception("API returned success but 'url' field is missing")
                else:
                    logger.error("Failed to get video streaming URL for camera %s: %s", camera_id, result_desc)
                    raise Exception(f"Failed to get video streaming URL: {result_desc}")
            except httpx.HTTPError as e:
                logger.error("Failed to get video streaming URL for camera %s: %s", camera_id, str(e))
                raise

    async def upload_status(self)->None:
        url = self._cfg.report.status_report_url
        payload = {
            "algorithmType": 1,
            "bindId": self.stream_id,
            "cameraId": self.stream_name,
            "status": self._status.value,
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                logger.info("Status report sent for stream %s", self.stream_id)
            except httpx.HTTPError as e:
                logger.error("Failed to send status report for stream %s: %s", self.stream_id, str(e))

    async def _run_loop(self,rtsp_url:str) -> None:
        interval = self._compute_interval()
        reconnect_delay = 2.0
        max_reconnect_delay = 30.0

        while self._status == MonitorStatus.RUNNING:
            cap = cv2.VideoCapture(rtsp_url)
            if not cap.isOpened():
                self._status = MonitorStatus.ERROR
                logger.warning(
                    "Cannot open RTSP stream %s (%s), retrying in %.0fs…",
                    self._cfg.bindId, rtsp_url, reconnect_delay,
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                continue

            reconnect_delay = 2.0
            self._status = MonitorStatus.RUNNING
            logger.info("Connected to RTSP stream %s (%s)", self._cfg.bindId, rtsp_url)

            try:
                while self._status == MonitorStatus.RUNNING:
                    ret, frame = await asyncio.to_thread(cap.read)
                    if not ret:
                        logger.warning("Lost RTSP stream %s, reconnecting…", self._cfg.bindId)
                        self._status = MonitorStatus.ERROR
                        break

                    # JPEG encode
                    ok, buf = cv2.imencode(".jpg", frame)
                    if not ok:
                        continue
                    image_bytes = buf.tobytes()
                    ts_ms = int(time.time() * 1000)
                    self.latest_frame_ts = ts_ms

                    task = Task(
                        bindId=self._cfg.bindId,
                        cameraId=self._cfg.cameraId,
                        image_data=image_bytes,
                        timestamp_ms=ts_ms,
                        target_labels=self._cfg.labels,
                        status_report_url= self._cfg.report.status_report_url,
                        result_report_url= self._cfg.report.result_report_url,
                    )

                    # Backpressure: drop the oldest queued frame when the queue
                    # is full so that the monitor stays real-time.
                    if self._queue.full():
                        try:
                            self._queue.get_nowait()
                            logger.warning(
                                "Queue full – dropped oldest frame for stream %s",
                                self._cfg.bindId,
                            )
                        except asyncio.QueueEmpty:
                            pass

                    try:
                        self._queue.put_nowait(task)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Queue still full – dropping current frame for stream %s",
                            self._cfg.bindId,
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
                self.tasks[s.bindId] = StreamTask(s, queue)

    async def start_all(self) -> None:
        for t in self.tasks.values():
            await t.start()

    async def stop_all(self) -> None:
        for t in self.tasks.values():
            await t.stop()

    async def init_single_stream(self,stream_cfg: StreamConfig) -> None:
        if stream_cfg.enabled:
            self.tasks[stream_cfg.bindId] = StreamTask(stream_cfg, self._queue)

    async def remove_single_stream(self, bind_id: str) -> bool:
        """停止并移除指定 bindId 的流任务，返回是否找到该任务。"""
        task = self.tasks.pop(bind_id, None)
        if task is None:
            return False
        await task.stop()
        return True

