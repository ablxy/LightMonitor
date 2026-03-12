"""Tests for the YAML configuration loader."""

import os
import tempfile

import pytest
import yaml

from app.config import AppConfig, load_config


@pytest.fixture
def sample_yaml(tmp_path):
    """Write a minimal valid config YAML and return its path."""
    cfg = {
        "streams": [
            {
                "id": "test-01",
                "name": "Test Camera",
                "rtsp_url": "rtsp://127.0.0.1/test",
                "enabled": True,
                "frame_extraction": {"fps": 2},
                "labels": ["person", "car"],
            }
        ],
        "detection": {
            "model_url": "http://localhost:8501/v1/models/yolo:predict",
            "auth": {"type": "api_key", "token": "secret"},
            "confidence_threshold": 0.6,
        },
        "alarm": {
            "enabled": True,
            "webhook_url": "http://alerts.example.com/api",
            "auth": {"type": "bearer", "token": "tok"},
        },
        "queue": {"maxsize": 50},
        "minio": {
            "endpoint": "minio:9000",
            "access_key": "user",
            "secret_key": "pass",
            "bucket": "mybucket",
            "secure": True,
        },
        "logging": {
            "jsonl_path": "/var/log/detections.jsonl",
            "rotate_when": "midnight",
            "backup_count": 14,
        },
        "server": {"host": "0.0.0.0", "port": 8000},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(cfg))
    return str(path)


def test_load_config_full(sample_yaml):
    config = load_config(sample_yaml)
    assert len(config.streams) == 1
    assert config.streams[0].id == "test-01"
    assert config.streams[0].name == "Test Camera"
    assert config.streams[0].rtsp_url == "rtsp://127.0.0.1/test"
    assert config.streams[0].frame_extraction.fps == 2
    assert config.streams[0].labels == ["person", "car"]
    assert config.detection.confidence_threshold == 0.6
    assert config.detection.auth.type == "api_key"
    assert config.alarm.enabled is True
    assert config.alarm.webhook_url == "http://alerts.example.com/api"
    assert config.queue.maxsize == 50
    assert config.minio.endpoint == "minio:9000"
    assert config.minio.bucket == "mybucket"
    assert config.minio.secure is True
    assert config.logging.jsonl_path == "/var/log/detections.jsonl"
    assert config.logging.backup_count == 14
    assert config.server.port == 8000


def test_load_config_defaults(tmp_path):
    """Config with only required fields should still parse with defaults."""
    cfg = {
        "streams": [
            {
                "id": "s1",
                "name": "S1",
                "rtsp_url": "rtsp://x",
            }
        ]
    }
    path = tmp_path / "min.yaml"
    path.write_text(yaml.dump(cfg))
    config = load_config(str(path))
    assert config.detection.confidence_threshold == 0.5
    assert config.alarm.enabled is False
    assert config.queue.maxsize == 100
    assert config.minio.endpoint == "localhost:9000"
    assert config.minio.bucket == "lightmonitor"
    assert config.logging.rotate_when == "midnight"
    assert config.logging.backup_count == 7
    assert config.server.host == "0.0.0.0"


def test_load_config_empty(tmp_path):
    """Empty YAML should produce a valid config with defaults."""
    path = tmp_path / "empty.yaml"
    path.write_text("")
    config = load_config(str(path))
    assert config.streams == []
    assert config.detection.confidence_threshold == 0.5


def test_load_config_multiple_streams(tmp_path):
    cfg = {
        "streams": [
            {"id": "a", "name": "A", "rtsp_url": "rtsp://a", "labels": ["fire"]},
            {"id": "b", "name": "B", "rtsp_url": "rtsp://b", "enabled": False},
        ]
    }
    path = tmp_path / "multi.yaml"
    path.write_text(yaml.dump(cfg))
    config = load_config(str(path))
    assert len(config.streams) == 2
    assert config.streams[0].enabled is True
    assert config.streams[1].enabled is False
    assert config.streams[0].labels == ["fire"]
    assert config.streams[1].labels == []
