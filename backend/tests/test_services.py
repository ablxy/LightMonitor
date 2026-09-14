"""Tests for queue, shared-source, and detection behavior."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from app.config import (
    AppConfig,
    FrameExtractionConfig,
    ReportConfig,
    StorageConfig,
    StreamConfig,
)
from app.models import Task
from app.services.alarm import AlarmService
from app.services.detection import DetectionService
from app.services.monitor import MonitorService, StreamTask


def stream(bind_id: str, *, interval_s: float = 1.0) -> StreamConfig:
    return StreamConfig(
        bindId=bind_id,
        cameraId="camera-1",
        live_url="rtsp://camera/live",
        labels=[bind_id],
        frame_extraction=FrameExtractionConfig(interval_s=interval_s),
        report=ReportConfig(),
    )


def config(tmp_path, streams=None) -> AppConfig:
    return AppConfig(
        streams=streams or [],
        storage=StorageConfig(
            db_path=str(tmp_path / "history.db"),
            snapshots_dir=str(tmp_path / "snapshots"),
        ),
    )


def test_stream_task_interval():
    queue = asyncio.Queue(maxsize=4)
    source = StreamTask(stream("task-1", interval_s=0.5), queue)
    assert source._compute_interval() == 0.5


def test_same_camera_reuses_one_physical_source(tmp_path):
    first = stream("person", interval_s=1)
    second = stream("fire", interval_s=5)
    monitor = MonitorService(config(tmp_path, [first, second]), asyncio.Queue(8))
    assert len(monitor._sources) == 1
    assert set(monitor.tasks) == {"person", "fire"}
    assert monitor._sources["camera-1"].binding_count == 2


@pytest.mark.asyncio
async def test_same_frame_runs_model_once_for_multiple_bindings(tmp_path):
    cfg = config(tmp_path)
    alarm = AlarmService()
    database = AsyncMock()
    service = DetectionService(cfg, alarm, asyncio.Queue(8), database)
    service._call_model = AsyncMock(
        return_value=[{"label": "person", "confidence": 0.6}]
    )
    first = Task(
        frame_id="frame-1",
        bindId="person",
        cameraId="camera-1",
        timestamp_ms=1,
        image_data=b"jpeg",
        target_labels=[],
        confidence_threshold=0.5,
    )
    second = Task(
        frame_id="frame-1",
        bindId="fire",
        cameraId="camera-1",
        timestamp_ms=1,
        image_data=b"jpeg",
        target_labels=[],
        confidence_threshold=0.8,
    )
    await asyncio.gather(service._process_task(first), service._process_task(second))
    assert service._call_model.await_count == 1
    assert len(service.get_recent_results("person")[0].detections) == 1
    assert service.get_recent_results("fire")[0].detections == []
    await service.close()
    await alarm.close()


@pytest.mark.asyncio
async def test_expired_frame_is_discarded_and_accounted_for(tmp_path):
    cfg = config(tmp_path)
    cfg.queue.max_frame_age_s = 0.01
    queue = asyncio.Queue(4)
    alarm = AlarmService()
    service = DetectionService(cfg, alarm, queue, AsyncMock(), num_workers=1)
    await service.start()
    task = Task(
        bindId="task-1",
        cameraId="camera-1",
        timestamp_ms=1,
        image_data=b"jpeg",
        target_labels=[],
        enqueued_at=0,
    )
    await queue.put(task)
    await asyncio.wait_for(queue.join(), timeout=1)
    assert service.get_recent_results("task-1") == []
    await service.close()
    await alarm.close()


@pytest.mark.asyncio
async def test_remove_one_binding_keeps_shared_source(tmp_path):
    monitor = MonitorService(
        config(tmp_path, [stream("person"), stream("fire")]),
        asyncio.Queue(8),
    )
    assert await monitor.remove_single_stream("person") is True
    assert "fire" in monitor.tasks
    assert len(monitor._sources) == 1
