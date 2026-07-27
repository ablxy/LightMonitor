"""Flask compatibility gateway for the incremental migration.

This module is intentionally independent from the legacy FastAPI lifespan. It
can be mounted as a separate process while the existing pipeline remains live.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from flask import Flask, jsonify, request


def _basic_auth_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        expected = getattr(request, "app_config", {})
        username = getattr(expected, "username", None) or expected.get("username", "maasadmin")
        password = getattr(expected, "password", None) or expected.get("password", "Maas@dj0086")
        auth = request.authorization
        if auth is None or auth.username != username or auth.password != password:
            return jsonify({"detail": "Invalid credentials"}), 401, {
                "WWW-Authenticate": 'Basic realm="LightMonitor"'
            }
        return view(*args, **kwargs)

    return wrapped


def create_app(
    *,
    config: Any | None = None,
    monitor_service: Any | None = None,
    detection_service: Any | None = None,
    repository: Any | None = None,
) -> Flask:
    """Create the transitional Flask app with injectable service dependencies."""
    app = Flask(__name__)
    app.config["lightmonitor_config"] = config
    app.extensions["monitor_service"] = monitor_service
    app.extensions["detection_service"] = detection_service
    app.extensions["alarm_repository"] = repository

    @app.before_request
    def expose_config() -> None:
        cfg = app.config.get("lightmonitor_config")
        request.app_config = getattr(cfg, "api_auth", {}) if cfg else {}

    @app.get("/health")
    @app.get("/-/health")
    def health() -> Any:
        return jsonify({"status": "ok", "service": "web"})

    @app.get("/-/ready")
    def ready() -> Any:
        missing = []
        if app.extensions.get("monitor_service") is None:
            missing.append("monitor")
        if app.extensions.get("detection_service") is None:
            missing.append("detection")
        if missing:
            return jsonify({"status": "not_ready", "missing": missing}), 503
        return jsonify({"status": "ready"})

    @app.get("/api/v1/tasks")
    @_basic_auth_required
    def list_tasks() -> Any:
        monitor = app.extensions.get("monitor_service")
        if monitor is None:
            return jsonify({"detail": "Service not ready"}), 503
        result = []
        for task in monitor.tasks.values():
            status = task._status.value if hasattr(task._status, "value") else str(task._status)
            result.append(
                {
                    "stream_id": task.stream_id,
                    "stream_name": task.stream_name,
                    "status": status,
                    "labels": task.labels,
                    "latest_frame_ts": task.latest_frame_ts,
                }
            )
        return jsonify(result)

    @app.get("/api/v1/tasks/<task_id>/results")
    @_basic_auth_required
    def task_results(task_id: str) -> Any:
        detection = app.extensions.get("detection_service")
        if detection is None:
            return jsonify({"detail": "Service not ready"}), 503
        if task_id not in getattr(app.extensions.get("monitor_service"), "tasks", {}):
            return jsonify({"detail": "Task not found"}), 404
        try:
            limit = min(max(int(request.args.get("limit", 20)), 1), 100)
        except ValueError:
            return jsonify({"detail": "limit must be an integer"}), 400
        results = detection.get_recent_results(task_id, limit=limit)
        return jsonify([item.model_dump(mode="json") for item in results])

    return app
