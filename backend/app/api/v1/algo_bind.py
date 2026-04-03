"""API endpoints for algorithm binding (Table 1-18)."""

from fastapi import APIRouter, HTTPException , Depends
from app.models import BindRequest, BindResponse, UnbindRequest
from app.services.monitor import MonitorService
from app.config import  StreamConfig,ReportConfig
from app.api.v1.algo_auth import verify_md5_signature
import logging


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-video-analysis/ai/v1/api/algorithm", tags=["Algorithm Binding and UnBinding"])


_monitor_service: MonitorService | None = None

def init_binding_router(monitor_service: MonitorService):
    global _monitor_service
    _monitor_service = monitor_service

@router.post("/bind", response_model=BindResponse , dependencies=[Depends(verify_md5_signature)])
async def bind_algorithm(req: BindRequest):
    """
    Table 1-18: Algorithm platform binds functionality to a camera.
    Receives RTSP URL (liveUrl) and configuration, dynamically adds a stream task.
    """
    if not _monitor_service:
        raise HTTPException(status_code=503, detail="Monitor service not initialized")

    # Adapt BindRequest to internal StreamConfig
    # bindId -> bindId (Unique Task ID)
    # cameraId -> cameraId (Display Name)
    # liveUrl -> rtsp_url
    # algorithmList -> labels (Simple mapping strategy: use algo codes as labels or map them)
    labels = req.algorithmList 

    report_config = ReportConfig(
        status_report_url=req.report.status_report_url,   # type: ignore
        result_report_url=req.report.result_report_url   # type: ignore
)

    stream_cfg = StreamConfig(
        bindId=req.bindId,
        cameraId=req.cameraId,
        live_url=req.liveUrl,
        enabled=True,
        labels=labels,
        # Determine FPS/Interval from configuation if needed, or default
        report=report_config
    )
    
    # If specific threshold config is passed
    if req.configuation:
        # You might store these extra configs in a extended StreamConfig
        # For now, we just log them or use defaults
        pass

    try:
        logging.info("Successfully bound camera %s with bindId %s to algorithms %s", req.cameraId, req.bindId, req.algorithmList)
        await _monitor_service.init_single_stream(stream_cfg)
    except Exception as e:
        return BindResponse(resultCode=1, resultDesc=f"Failed to bind: {str(e)}")

    return BindResponse(resultCode=0, resultDesc="SUCCESS")


@router.post("/unbind", dependencies=[Depends(verify_md5_signature)])
async def unbind_algorithm(req: UnbindRequest):
    """
    算法解绑接口：停止并移除指定 bindId 的流任务。
    """
    if not _monitor_service:
        raise HTTPException(status_code=503, detail="Monitor service not initialized")

    found = await _monitor_service.remove_single_stream(req.bindId)
    if not found:
        return {"resultCode": 1, "resultDesc": f"bindId {req.bindId} 不存在"}

    logging.info("Successfully unbound bindId %s (cameraId %s)", req.bindId, req.cameraId)
    return {"resultCode": 0, "resultDesc": "解绑1条数据"}