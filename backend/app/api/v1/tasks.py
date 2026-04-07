"""REST API v1 – task, result, and history endpoints for the frontend."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from app.models import FrameResult, HistoryRecord, TaskDetail, TaskStatus

router = APIRouter(prefix="/api/v1", tags=["tasks"])

# These will be set by main.py at startup
_monitor_service = None
_detection_service = None
_db_service = None
_snapshots_dir: str = "data/snapshots"


def init_router(monitor_service, detection_service, db_service, snapshots_dir: str = "data/snapshots") -> None:  # noqa: ANN001
    global _monitor_service, _detection_service, _db_service, _snapshots_dir
    _monitor_service = monitor_service
    _detection_service = detection_service
    _db_service = db_service
    _snapshots_dir = snapshots_dir


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

@router.get("/history")
async def get_history(
    stream_id: str | None = Query(None, description="Filter by stream ID"),
    start_ms: int | None = Query(None, description="Start timestamp (ms, inclusive)"),
    end_ms: int | None = Query(None, description="End timestamp (ms, inclusive)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
):
    """Return history records from SQLite, filtered by stream ID and/or time range."""
    if _db_service is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    records = await _db_service.query_records(
        stream_id=stream_id,
        start_ms=start_ms,
        end_ms=end_ms,
        limit=limit,
    )
    return records


# ------------------------------------------------------------------
# GET /api/v1/snapshots/{stream_id}/{filename}
# ------------------------------------------------------------------

@router.get("/snapshots/{stream_id}/{filename}")
async def get_snapshot(stream_id: str, filename: str):
    """Serve a stored snapshot image from the local filesystem."""
    # Prevent path traversal
    if ".." in stream_id or ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid path")

    if _snapshots_dir is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    filepath = os.path.join(_snapshots_dir, stream_id, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return FileResponse(filepath, media_type="image/jpeg")

