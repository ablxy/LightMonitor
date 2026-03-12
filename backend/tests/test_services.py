"""Tests for service-layer logic (detection, alarm, monitor helpers)."""

import asyncio
import collections
import json
import os

import pytest
import yaml

from app.config import load_config
from app.models import BoundingBox, DetectionResult, FrameResult, Task
from app.services.alarm import AlarmService
from app.services.detection import DetectionService


@pytest.fixture
def config(tmp_path):
    cfg = {
        "streams": [
            {
                "id": "s1",
                "name": "Stream 1",
                "rtsp_url": "rtsp://127.0.0.1/s1",
                "enabled": True,
                "labels": ["person"],
            }
        ],
        "detection": {"model_url": "", "confidence_threshold": 0.5},
        "alarm": {"enabled": False},
        "queue": {"maxsize": 10},
        "minio": {
            "endpoint": "localhost:9000",
            "access_key": "minioadmin",
            "secret_key": "minioadmin",
            "bucket": "test-bucket",
            "secure": False,
        },
        "logging": {
            "jsonl_path": str(tmp_path / "detections.jsonl"),
            "rotate_when": "midnight",
            "backup_count": 7,
        },
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(cfg))
    return load_config(str(path))


@pytest.fixture
def queue():
    return asyncio.Queue(maxsize=10)


@pytest.fixture
def detection_service(config, queue):
    alarm = AlarmService(config.alarm)
    return DetectionService(config, alarm, queue)


@pytest.mark.asyncio
async def test_process_task_no_model(detection_service, queue):
    """When model_url is empty, processing a task should succeed with zero detections."""
    task = Task(
        stream_id="s1",
        stream_name="Stream 1",
        image_data=b"\xff\xd8\xff\xe0",
        timestamp_ms=1700000000000,
        target_labels=["person"],
    )
    await queue.put(task)
    await detection_service._process_task(task)

    results = detection_service.get_recent_results("s1")
    assert len(results) == 1
    assert results[0].stream_id == "s1"
    assert results[0].detections == []
    assert results[0].alarmed is False


@pytest.mark.asyncio
async def test_get_recent_results(detection_service, queue):
    """Results should be stored and retrievable."""
    for ts in [1000, 2000]:
        task = Task(
            stream_id="s1",
            stream_name="Stream 1",
            image_data=b"\xff",
            timestamp_ms=ts,
            target_labels=[],
        )
        await detection_service._process_task(task)

    results = detection_service.get_recent_results("s1")
    assert len(results) == 2
    # Most recent first
    assert results[0].timestamp_ms == 2000
    assert results[1].timestamp_ms == 1000


@pytest.mark.asyncio
async def test_get_recent_results_limit(detection_service, queue):
    for i in range(10):
        task = Task(
            stream_id="s1",
            stream_name="Stream 1",
            image_data=b"\xff",
            timestamp_ms=i,
            target_labels=[],
        )
        await detection_service._process_task(task)

    results = detection_service.get_recent_results("s1", limit=3)
    assert len(results) == 3


def test_alarm_service_disabled():
    """AlarmService should be creatable with alarm disabled."""
    from app.config import AlarmConfig
    cfg = AlarmConfig(enabled=False)
    svc = AlarmService(cfg)
    assert svc._config.enabled is False


def test_monitor_service_creates_tasks(config, queue):
    from app.services.monitor import MonitorService
    mon = MonitorService(config, queue)
    assert "s1" in mon.tasks
    assert mon.tasks["s1"].status == "offline"


def test_stream_task_interval():
    """StreamTask should compute interval from fps or interval_s."""
    from app.config import StreamConfig, FrameExtractionConfig
    from app.services.monitor import StreamTask

    q = asyncio.Queue(maxsize=10)

    s1 = StreamConfig(
        id="x", name="X", rtsp_url="rtsp://x",
        frame_extraction=FrameExtractionConfig(fps=2),
    )
    t1 = StreamTask(s1, q)
    assert t1._compute_interval() == 0.5

    s2 = StreamConfig(
        id="y", name="Y", rtsp_url="rtsp://y",
        frame_extraction=FrameExtractionConfig(interval_s=10),
    )
    t2 = StreamTask(s2, q)
    assert t2._compute_interval() == 10.0

    # Default when nothing is set
    s3 = StreamConfig(id="z", name="Z", rtsp_url="rtsp://z")
    t3 = StreamTask(s3, q)
    assert t3._compute_interval() == 1.0


def test_queue_backpressure(config, queue):
    """When queue is full, put_nowait should raise QueueFull."""
    # Fill the queue to capacity
    for i in range(10):
        task = Task(
            stream_id="s1",
            stream_name="Stream 1",
            image_data=b"\xff",
            timestamp_ms=i,
            target_labels=[],
        )
        queue.put_nowait(task)

    assert queue.full()

    # Drop oldest (mimic monitor backpressure)
    dropped = queue.get_nowait()
    assert dropped.timestamp_ms == 0

    # Now there is space for one more
    new_task = Task(
        stream_id="s1",
        stream_name="Stream 1",
        image_data=b"\xff",
        timestamp_ms=9999,
        target_labels=[],
    )
    queue.put_nowait(new_task)
    assert queue.qsize() == 10
