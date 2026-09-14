"""Logging and public error-output tests."""

import json
import logging

from app.config import AppConfig, LoggingConfig, StorageConfig
from app.observability import JsonFormatter
from fastapi.testclient import TestClient


def test_json_formatter_redacts_secrets():
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "password=hidden token:secret",
        (),
        None,
    )
    payload = json.loads(JsonFormatter().format(record))
    assert "hidden" not in payload["message"]
    assert "secret" not in payload["message"]
    assert "password=***" in payload["message"]


def test_request_id_is_returned_in_errors(monkeypatch, tmp_path):
    from app import main

    config = AppConfig(
        storage=StorageConfig(
            db_path=str(tmp_path / "app.db"),
            snapshots_dir=str(tmp_path / "snapshots"),
        ),
        logging=LoggingConfig(console_enabled=False, file_enabled=False),
    )
    monkeypatch.setattr(main, "get_config", lambda: config)

    with TestClient(main.app) as client:
        response = client.get(
            "/api/v1/tasks", headers={"X-Request-ID": "request-test-1"}
        )

    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == "request-test-1"
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.json()["error"]["request_id"] == "request-test-1"
