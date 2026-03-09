"""Tests for service-layer logic (detection, alarm, monitor helpers)."""

import asyncio
import collections

import pytest
import yaml

from app.config import load_config
from app.models import BoundingBox, DetectionResult, FrameResult
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
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(cfg))
    return load_config(str(path))


@pytest.fixture
def detection_service(config):
    alarm = AlarmService(config.alarm)
    return DetectionService(config, alarm)


@pytest.mark.asyncio
async def test_process_frame_no_model(detection_service):
    """When model_url is empty, process_frame should still succeed with zero detections."""
    result = await detection_service.process_frame(
        stream_id="s1",
        stream_name="Stream 1",
        image_data=b"\xff\xd8\xff\xe0",  # fake JPEG header
        timestamp_ms=1700000000000,
        target_labels=["person"],
    )
    assert result.stream_id == "s1"
    assert result.detections == []
    assert result.alarmed is False


@pytest.mark.asyncio
async def test_get_recent_results(detection_service):
    """Results should be stored and retrievable."""
    await detection_service.process_frame(
        stream_id="s1",
        stream_name="Stream 1",
        image_data=b"\xff",
        timestamp_ms=1000,
        target_labels=[],
    )
    await detection_service.process_frame(
        stream_id="s1",
        stream_name="Stream 1",
        image_data=b"\xff",
        timestamp_ms=2000,
        target_labels=[],
    )
    results = detection_service.get_recent_results("s1")
    assert len(results) == 2
    # Most recent first
    assert results[0].timestamp_ms == 2000
    assert results[1].timestamp_ms == 1000


@pytest.mark.asyncio
async def test_get_recent_results_limit(detection_service):
    for i in range(10):
        await detection_service.process_frame(
            stream_id="s1",
            stream_name="Stream 1",
            image_data=b"\xff",
            timestamp_ms=i,
            target_labels=[],
        )
    results = detection_service.get_recent_results("s1", limit=3)
    assert len(results) == 3


def test_alarm_service_disabled():
    """AlarmService should be creatable with alarm disabled."""
    from app.config import AlarmConfig
    cfg = AlarmConfig(enabled=False)
    svc = AlarmService(cfg)
    assert svc._config.enabled is False


def test_monitor_service_creates_tasks(config):
    from app.services.monitor import MonitorService
    mon = MonitorService(config)
    assert "s1" in mon.tasks
    assert mon.tasks["s1"].status == "offline"


def test_stream_task_interval():
    """StreamTask should compute interval from fps or interval_s."""
    from app.config import StreamConfig, FrameExtractionConfig
    from app.services.monitor import StreamTask

    s1 = StreamConfig(
        id="x", name="X", rtsp_url="rtsp://x",
        frame_extraction=FrameExtractionConfig(fps=2),
    )
    t1 = StreamTask(s1, "localhost:50051")
    assert t1._compute_interval() == 0.5

    s2 = StreamConfig(
        id="y", name="Y", rtsp_url="rtsp://y",
        frame_extraction=FrameExtractionConfig(interval_s=10),
    )
    t2 = StreamTask(s2, "localhost:50051")
    assert t2._compute_interval() == 10.0

    # Default when nothing is set
    s3 = StreamConfig(id="z", name="Z", rtsp_url="rtsp://z")
    t3 = StreamTask(s3, "localhost:50051")
    assert t3._compute_interval() == 1.0
