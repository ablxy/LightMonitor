"""Tests for REST API endpoints (tasks router)."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig, load_config
from app.models import FrameResult, DetectionResult, BoundingBox
from app.services.alarm import AlarmService
from app.services.detection import DetectionService
from app.services.monitor import MonitorService
from app.api.v1.tasks import init_router, router
from fastapi import FastAPI

import yaml


@pytest.fixture
def config(tmp_path):
    cfg = {
        "streams": [
            {
                "id": "cam-1",
                "name": "Camera 1",
                "rtsp_url": "rtsp://127.0.0.1/cam1",
                "enabled": True,
                "frame_extraction": {"fps": 1},
                "labels": ["person"],
            },
            {
                "id": "cam-2",
                "name": "Camera 2",
                "rtsp_url": "rtsp://127.0.0.1/cam2",
                "enabled": True,
                "frame_extraction": {"interval_s": 5},
                "labels": ["car"],
            },
        ],
        "detection": {"model_url": "", "confidence_threshold": 0.5},
        "alarm": {"enabled": False},
        "queue": {"maxsize": 10},
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(cfg))
    return load_config(str(path))


@pytest.fixture
def client(config, tmp_path):
    queue = asyncio.Queue(maxsize=config.queue.maxsize)
    alarm = AlarmService(config.alarm)
    detection = DetectionService(config, alarm, queue)
    monitor = MonitorService(config, queue)

    jsonl_path = str(tmp_path / "detections.jsonl")
    app = FastAPI()
    init_router(monitor, detection, jsonl_path=jsonl_path)
    app.include_router(router)
    return TestClient(app), jsonl_path


def test_list_tasks(client):
    tc, _ = client
    resp = tc.get("/api/v1/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    ids = {t["stream_id"] for t in data}
    assert ids == {"cam-1", "cam-2"}


def test_get_task_detail(client):
    tc, _ = client
    resp = tc.get("/api/v1/tasks/cam-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task"]["stream_id"] == "cam-1"
    assert data["task"]["stream_name"] == "Camera 1"
    assert isinstance(data["recent_results"], list)


def test_get_task_not_found(client):
    tc, _ = client
    resp = tc.get("/api/v1/tasks/nonexistent")
    assert resp.status_code == 404


def test_get_results_empty(client):
    tc, _ = client
    resp = tc.get("/api/v1/tasks/cam-1/results")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_results_not_found(client):
    tc, _ = client
    resp = tc.get("/api/v1/tasks/nope/results")
    assert resp.status_code == 404


def test_history_empty(client):
    """History endpoint returns empty body when JSONL file doesn't exist."""
    tc, _ = client
    resp = tc.get("/api/v1/history")
    assert resp.status_code == 200
    assert resp.text == ""


def test_history_returns_records(client):
    """History endpoint streams records from JSONL file."""
    tc, jsonl_path = client
    records = [
        {"task_id": "t1", "timestamp_ms": 1000, "stream_id": "cam-1",
         "stream_name": "Camera 1", "detections": [], "image_url": "http://minio/1"},
        {"task_id": "t2", "timestamp_ms": 2000, "stream_id": "cam-2",
         "stream_name": "Camera 2", "detections": [], "image_url": "http://minio/2"},
    ]
    with open(jsonl_path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    resp = tc.get("/api/v1/history")
    assert resp.status_code == 200
    lines = [l for l in resp.text.splitlines() if l.strip()]
    assert len(lines) == 2


def test_history_filter_stream_id(client):
    """History endpoint filters by stream_id."""
    tc, jsonl_path = client
    records = [
        {"task_id": "t1", "timestamp_ms": 1000, "stream_id": "cam-1",
         "stream_name": "Camera 1", "detections": [], "image_url": ""},
        {"task_id": "t2", "timestamp_ms": 2000, "stream_id": "cam-2",
         "stream_name": "Camera 2", "detections": [], "image_url": ""},
    ]
    with open(jsonl_path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    resp = tc.get("/api/v1/history?stream_id=cam-1")
    assert resp.status_code == 200
    lines = [l for l in resp.text.splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["stream_id"] == "cam-1"


def test_history_filter_time_range(client):
    """History endpoint filters by start_ms and end_ms."""
    tc, jsonl_path = client
    records = [
        {"task_id": "t1", "timestamp_ms": 1000, "stream_id": "cam-1",
         "stream_name": "Camera 1", "detections": [], "image_url": ""},
        {"task_id": "t2", "timestamp_ms": 5000, "stream_id": "cam-1",
         "stream_name": "Camera 1", "detections": [], "image_url": ""},
        {"task_id": "t3", "timestamp_ms": 9000, "stream_id": "cam-1",
         "stream_name": "Camera 1", "detections": [], "image_url": ""},
    ]
    with open(jsonl_path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    resp = tc.get("/api/v1/history?start_ms=2000&end_ms=8000")
    assert resp.status_code == 200
    lines = [l for l in resp.text.splitlines() if l.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["task_id"] == "t2"


def test_history_limit(client):
    """History endpoint respects the limit parameter."""
    tc, jsonl_path = client
    with open(jsonl_path, "w") as f:
        for i in range(10):
            r = {"task_id": f"t{i}", "timestamp_ms": i * 1000,
                 "stream_id": "cam-1", "stream_name": "Camera 1",
                 "detections": [], "image_url": ""}
            f.write(json.dumps(r) + "\n")

    resp = tc.get("/api/v1/history?limit=3")
    assert resp.status_code == 200
    lines = [l for l in resp.text.splitlines() if l.strip()]
    assert len(lines) == 3
