"""Configuration compatibility and defaults."""

import yaml
from app.config import load_config


def test_loads_current_stream_schema(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "streams": [
                    {
                        "bindId": "task-1",
                        "cameraId": "camera-1",
                        "live_url": "rtsp://camera/live",
                        "labels": ["person"],
                        "report": {},
                    }
                ],
                "queue": {"maxsize": 16, "workers": 2},
                "logging": {"file_path": "/tmp/lightmonitor.log"},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(str(path))
    assert config.streams[0].bindId == "task-1"
    assert config.streams[0].cameraId == "camera-1"
    assert config.queue.maxsize == 16
    assert config.queue.workers == 2
    assert config.logging.resolved_file_path == "/tmp/lightmonitor.log"


def test_loads_legacy_stream_field_names(tmp_path):
    path = tmp_path / "legacy.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "streams": [
                    {"id": "legacy-1", "name": "Legacy", "rtsp_url": "rtsp://old"}
                ]
            }
        ),
        encoding="utf-8",
    )
    stream = load_config(str(path)).streams[0]
    assert stream.bindId == "legacy-1"
    assert stream.cameraId == "Legacy"
    assert stream.live_url == "rtsp://old"
    assert stream.report.status_report_url is None


def test_empty_config_uses_safe_defaults(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    config = load_config(str(path))
    assert config.streams == []
    assert config.logging.level == "INFO"
    assert config.queue.max_frame_age_s == 5.0
    assert config.queue.alarm_workers == 2


def test_legacy_jsonl_path_resolves_app_log_next_to_it(tmp_path):
    path = tmp_path / "config.yaml"
    detections = tmp_path / "logs" / "detections.jsonl"
    path.write_text(
        yaml.safe_dump({"logging": {"jsonl_path": str(detections)}}),
        encoding="utf-8",
    )
    config = load_config(str(path))
    assert config.logging.resolved_file_path == str(tmp_path / "logs" / "app.log")
