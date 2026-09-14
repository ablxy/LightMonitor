"""API endpoints for algorithm binding (Table 1-18)."""

import logging

from fastapi import APIRouter, Depends

from app.api.v1.algo_auth import verify_basic_auth
from app.config import StreamConfig
from app.errors import ServiceNotReadyError
from app.models import BindRequest, BindResponse, UnbindRequest, UnbindResponse
from app.services.monitor import MonitorService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/ai-video-analysis/ai/v1/api/algorithm",
    tags=["Algorithm Binding and UnBinding"],
)


_monitor_service: MonitorService | None = None


def init_binding_router(monitor_service: MonitorService) -> None:
    global _monitor_service
    _monitor_service = monitor_service


@router.post(
    "/bind", response_model=BindResponse, dependencies=[Depends(verify_basic_auth)]
)
async def bind_algorithm(req: BindRequest):
    """
    Table 1-18: Algorithm platform binds functionality to a camera.
    Receives RTSP URL (liveUrl) and configuration, dynamically adds a stream task.
    """
    if not _monitor_service:
        raise ServiceNotReadyError()

    # Adapt BindRequest to internal StreamConfig
    # bindId -> bindId (Unique Task ID)
    # cameraId -> cameraId (Display Name)
    # liveUrl -> rtsp_url
    # algorithmList -> labels (Simple mapping strategy: use algo codes as labels or map them)
    labels = req.algorithmList

    stream_cfg = StreamConfig(
        bindId=req.bindId,
        cameraId=req.cameraId,
        live_url=req.liveUrl,
        enabled=True,
        labels=labels,
        # Determine FPS/Interval from configuation if needed, or default
        report=req.report,
        confidence_threshold=(
            req.configuation.threshold / 100
            if req.configuation and req.configuation.threshold is not None
            else None
        ),
    )
    logger.info(
        "binding request received",
        extra={
            "event": "stream.binding.requested",
            "bind_id": req.bindId,
            "camera_id": req.cameraId,
            "algorithm_count": len(req.algorithmList),
        },
    )

    try:
        await _monitor_service.init_single_stream(stream_cfg)
    except ValueError as exc:
        logger.warning(
            "binding rejected",
            extra={
                "event": "stream.binding.rejected",
                "bind_id": req.bindId,
                "camera_id": req.cameraId,
                "error_type": type(exc).__name__,
            },
        )
        return BindResponse(resultCode=1, resultDesc=str(exc))
    except Exception as exc:
        logger.exception(
            "binding failed",
            extra={
                "event": "stream.binding.failed",
                "bind_id": req.bindId,
                "camera_id": req.cameraId,
                "error_type": type(exc).__name__,
            },
        )
        return BindResponse(resultCode=1, resultDesc="任务注册失败")

    logger.info(
        "binding accepted",
        extra={
            "event": "stream.binding.accepted",
            "bind_id": req.bindId,
            "camera_id": req.cameraId,
            "algorithm_count": len(req.algorithmList),
        },
    )
    return BindResponse(resultCode=0, resultDesc="SUCCESS")


@router.post(
    "/unbind", response_model=UnbindResponse, dependencies=[Depends(verify_basic_auth)]
)
async def unbind_algorithm(req: UnbindRequest):
    """
    算法解绑接口：停止并移除指定 bindId 的流任务。
    """
    if not _monitor_service:
        raise ServiceNotReadyError()

    found = await _monitor_service.remove_single_stream(req.bindId)
    if not found:
        logger.warning(
            "binding not found during unbind",
            extra={"event": "stream.binding.not_found", "bind_id": req.bindId},
        )
        return {"resultCode": 1, "resultDesc": f"bindId {req.bindId} 不存在"}

    logger.info(
        "binding removed",
        extra={
            "event": "stream.binding.removed",
            "bind_id": req.bindId,
            "camera_id": req.cameraId,
        },
    )
    return UnbindResponse(resultCode=0, resultDesc="解绑1条数据")
