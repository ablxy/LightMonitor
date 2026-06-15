"""API endpoints for algorithm binding (Table 1-18)."""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from app.models import BindRequest, BindResponse, UnbindRequest, UnbindResponse
from app.services.monitor import MonitorService
from app.config import StreamConfig, ReportConfig
import logging
import re

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-video-analysis/ai/v1/api/algorithm", tags=["Algorithm Binding and UnBinding"])


_monitor_service: MonitorService | None = None

def init_binding_router(monitor_service: MonitorService):
    global _monitor_service
    _monitor_service = monitor_service

@router.post("/bind", response_model=BindResponse)
async def bind_algorithm(req: BindRequest, background_tasks: BackgroundTasks):
    """
    Table 1-18: Algorithm platform binds functionality to a camera.
    Receives RTSP URL (liveUrl) and configuration, dynamically adds a stream task.
    """
    if not _monitor_service:
        raise HTTPException(status_code=503,
                            detail="Monitor service not initialized")

    # Adapt BindRequest to internal StreamConfig
    # bindId -> bindId (Unique Task ID)
    # cameraId -> cameraId (Display Name)
    # liveUrl -> rtsp_url
    # algorithmList -> labels (Simple mapping strategy: use algo codes as labels or map them)
    labels = req.algorithmList
    report_config = ReportConfig(
        # status_report_url=req.report.status_report_url,  # type: ignore
        # result_report_url=req.report.result_report_url  # type: ignore
    )

    # report_config = ReportConfig(
    #     status_report_url="http://172.23.31.245:10000/api/sapa/report/status",   # type: ignore
    #     result_report_url="http://172.23.31.245:10000/api/sapa/media/live"  # type: ignore
    # )

    # 入口的虚拟机实地址
    mapping_live_url = re.sub(r'(\d+\.\d+\.\d+\.)\d+', r'\g<1>245',
                              req.liveUrl)
    # mapping_live_url = "http://172.23.31.245:10000/api/sapa/media/live"

    logger.info("Mapping liveUrl from %s to %s for bindId %s", req.liveUrl,
                mapping_live_url, req.bindId)

    stream_cfg = StreamConfig(
        bindId=req.bindId,
        cameraId=req.cameraId,
        live_url=mapping_live_url,
        enabled=True,
        labels=labels,
        # Determine FPS/Interval from configuation if needed, or default
        report=report_config)
    logger.info("Received bind request: bindId=%s, cameraId=%s, algorithms=%s",
                req.bindId, req.cameraId, req.algorithmList)

    # If specific threshold config is passed
    if req.configuation:
        # You might store these extra configs in a extended StreamConfig
        # For now, we just log them or use defaults
        pass

    async def _do_bind():
        try:
            await _monitor_service.init_single_stream(stream_cfg)
            logger.info(
                "Successfully bound camera %s with bindId %s to algorithms %s",
                req.cameraId, req.bindId, req.algorithmList)
        except Exception as e:
            logger.error("Failed to bind camera %s with bindId %s: %s",
                         req.cameraId, req.bindId, str(e))

    background_tasks.add_task(_do_bind)
    return BindResponse(resultCode=0, resultDesc="SUCCESS")


@router.post("/unbind",response_model=UnbindResponse)
async def unbind_algorithm(req: UnbindRequest):
    """
    算法解绑接口：停止并移除指定 bindId 的流任务。
    """
    if not _monitor_service:
        logger.error("Attempted to unbind but Monitor service is not initialized")
        raise HTTPException(status_code=503, detail="Monitor service not initialized")

    found = await _monitor_service.remove_single_stream(req.bindId)
    if not found:
        logger.error("Attempted to unbind bindId %s but it was not found", req.bindId)
        return {"resultCode": 1, "resultDesc": f"bindId {req.bindId} 不存在"}

    logger.info("Successfully unbound bindId %s (cameraId %s)", req.bindId, req.cameraId)
    return {"resultCode": 0, "resultDesc": "解绑1条数据"}
