"""REST API contract tests."""

from typing import ClassVar
from unittest.mock import AsyncMock

import pytest
from app.api.v1.algo_auth import verify_basic_auth
from app.api.v1.tasks import init_router, router
from app.errors import install_exception_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TaskView:
    stream_id = "task-1"
    stream_name = "camera-1"
    status = "running"
    labels: ClassVar[list[str]] = ["person"]
    latest_frame_ts = 1_700_000_000_000


class Monitor:
    tasks: ClassVar[dict[str, TaskView]] = {"task-1": TaskView()}


class Detection:
    def get_recent_results(self, _task_id, limit=20):
        return []

    def get_last_error(self, _task_id):
        return None


@pytest.fixture
def client():
    database = AsyncMock()
    database.query_records.return_value = []
    init_router(Monitor(), Detection(), database, snapshots_dir="/tmp/snapshots")
    app = FastAPI()
    install_exception_handlers(app)
    app.dependency_overrides[verify_basic_auth] = lambda: True
    app.include_router(router)
    return TestClient(app), database


def test_list_tasks(client):
    test_client, _ = client
    response = test_client.get("/api/v1/tasks")
    assert response.status_code == 200
    assert response.json()[0]["stream_id"] == "task-1"
    assert response.json()[0]["last_error_code"] is None


def test_task_not_found_uses_error_envelope(client):
    test_client, _ = client
    response = test_client.get("/api/v1/tasks/missing")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "TASK_NOT_FOUND",
            "message": "任务不存在",
            "request_id": "-",
            "details": None,
        }
    }


def test_validation_error_uses_error_envelope(client):
    test_client, _ = client
    response = test_client.get("/api/v1/history?limit=0")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.json()["error"]["details"][0]["field"] == "query.limit"


def test_history_uses_database_service(client):
    test_client, database = client
    response = test_client.get("/api/v1/history?stream_id=task-1&limit=10")
    assert response.status_code == 200
    assert response.json() == []
    database.query_records.assert_awaited_once_with(
        stream_id="task-1", start_ms=None, end_ms=None, limit=10
    )
