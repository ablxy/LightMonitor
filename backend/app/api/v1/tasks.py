"""REST API v1 – task and result endpoints for the frontend."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.models import FrameResult, TaskDetail, TaskStatus

router = APIRouter(prefix="/api/v1", tags=["tasks"])

# These will be set by main.py at startup
_monitor_service = None
_detection_service = None


def init_router(monitor_service, detection_service) -> None:  # noqa: ANN001
    global _monitor_service, _detection_service
    _monitor_service = monitor_service
    _detection_service = detection_service


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
