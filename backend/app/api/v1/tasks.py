"""REST API v1 – task, result, and history endpoints for the frontend."""

from __future__ import annotations

import json
import os
from typing import Generator

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.models import FrameResult, HistoryRecord, TaskDetail, TaskStatus

router = APIRouter(prefix="/api/v1", tags=["tasks"])

# These will be set by main.py at startup
_monitor_service = None
_detection_service = None
_jsonl_path: str = "logs/detections.jsonl"


def init_router(monitor_service, detection_service, jsonl_path: str = "logs/detections.jsonl") -> None:  # noqa: ANN001
    global _monitor_service, _detection_service, _jsonl_path
    _monitor_service = monitor_service
    _detection_service = detection_service
    _jsonl_path = jsonl_path


# ------------------------------------------------------------------
# GET /api/v1/tasks
# ------------------------------------------------------------------

@router.get("/tasks", response_model=list[TaskStatus])
async def list_tasks():
    """Return all configured stream tasks and their current status."""
    if _monitor_service is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    tasks: list[TaskStatus] = []
    for sid, st in _monitor_service.tasks.items():
        tasks.append(
            TaskStatus(
                stream_id=st.stream_id,
                stream_name=st.stream_name,
                status=st.status,
                labels=st.labels,
                latest_frame_ts=st.latest_frame_ts,
            )
        )
    return tasks


# ------------------------------------------------------------------
# GET /api/v1/tasks/{task_id}
# ------------------------------------------------------------------

@router.get("/tasks/{task_id}", response_model=TaskDetail)
async def get_task(task_id: str):
    """Return status and recent results for a single task."""
    if _monitor_service is None or _detection_service is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    st = _monitor_service.tasks.get(task_id)
    if st is None:
        raise HTTPException(status_code=404, detail="Task not found")

    status = TaskStatus(
        stream_id=st.stream_id,
        stream_name=st.stream_name,
        status=st.status,
        labels=st.labels,
        latest_frame_ts=st.latest_frame_ts,
    )
    results = _detection_service.get_recent_results(task_id)
    return TaskDetail(task=status, recent_results=results)


# ------------------------------------------------------------------
# GET /api/v1/tasks/{task_id}/results
# ------------------------------------------------------------------

@router.get("/tasks/{task_id}/results", response_model=list[FrameResult])
async def get_results(
    task_id: str,
    limit: int = Query(20, ge=1, le=100),
):
    """Return recent detection results for a given task."""
    if _detection_service is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    if task_id not in (_monitor_service.tasks if _monitor_service else {}):
        raise HTTPException(status_code=404, detail="Task not found")

    return _detection_service.get_recent_results(task_id, limit=limit)


# ------------------------------------------------------------------
# GET /api/v1/history
# ------------------------------------------------------------------

def _iter_jsonl(
    path: str,
    stream_id: str | None,
    start_ms: int | None,
    end_ms: int | None,
    limit: int,
) -> Generator[str, None, None]:
    """Generator that reads the JSONL file line-by-line and filters records."""
    if not os.path.exists(path):
        return

    count = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Filter by stream_id
            if stream_id is not None and record.get("stream_id") != stream_id:
                continue
            # Filter by time range
            ts = record.get("timestamp_ms", 0)
            if start_ms is not None and ts < start_ms:
                continue
            if end_ms is not None and ts > end_ms:
                continue

            yield json.dumps(record) + "\n"
            count += 1
            if count >= limit:
                break


@router.get("/history")
async def get_history(
    stream_id: str | None = Query(None, description="Filter by stream ID"),
    start_ms: int | None = Query(None, description="Start timestamp (ms, inclusive)"),
    end_ms: int | None = Query(None, description="End timestamp (ms, inclusive)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
):
    """Stream JSONL history records filtered by stream ID and/or time range.

    The response is a JSON Lines stream (``Content-Type: application/x-ndjson``).
    Each line is a JSON-serialised :class:`HistoryRecord`.
    """

    def _generate():
        yield from _iter_jsonl(_jsonl_path, stream_id, start_ms, end_ms, limit)

    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
    )
