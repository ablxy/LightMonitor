"""Tests for REST API endpoints (tasks router)."""

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
        "grpc": {"detection_host": "localhost", "detection_port": 50051},
    }
    path = tmp_path / "cfg.yaml"
    path.write_text(yaml.dump(cfg))
    return load_config(str(path))


@pytest.fixture
def client(config):
    alarm = AlarmService(config.alarm)
    detection = DetectionService(config, alarm)
    monitor = MonitorService(config)

    app = FastAPI()
    init_router(monitor, detection)
    app.include_router(router)
    return TestClient(app)


def test_list_tasks(client):
    resp = client.get("/api/v1/tasks")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    ids = {t["stream_id"] for t in data}
    assert ids == {"cam-1", "cam-2"}


def test_get_task_detail(client):
    resp = client.get("/api/v1/tasks/cam-1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task"]["stream_id"] == "cam-1"
    assert data["task"]["stream_name"] == "Camera 1"
    assert isinstance(data["recent_results"], list)


def test_get_task_not_found(client):
    resp = client.get("/api/v1/tasks/nonexistent")
    assert resp.status_code == 404


def test_get_results_empty(client):
    resp = client.get("/api/v1/tasks/cam-1/results")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_results_not_found(client):
    resp = client.get("/api/v1/tasks/nope/results")
    assert resp.status_code == 404
