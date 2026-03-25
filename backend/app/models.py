"""Pydantic response models used across the API and services."""

from __future__ import annotations

from enum import Enum
import uuid

from pydantic import BaseModel, Field

from app.config import ReportConfig

class MonitorStatus(Enum):
    """
智能分析任务状态
0-初始化
1-启动中
2-正在运行
3-已停止
4-错误
    Args:
        Enum (_type_): _description_
    """
    INIT= 0
    STARTING = 1
    RUNNING = 2
    STOP = 3
    ERROR = 4

class BoundingBox(BaseModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class DetectionResult(BaseModel):
    label: str
    confidence: float
    bbox: BoundingBox | None = None


class FrameResult(BaseModel):
    stream_id: str
    stream_name: str
    timestamp_ms: int
    detections: list[DetectionResult] = Field(default_factory=list)
    alarmed: bool = False
    image_base64: str | None = None

class AlarmAttributes(BaseModel):
    """
    Table 1-22: 结构化属性。
    支持算法扩展属性，允许添加任何额外的 KV。
    """
    model_config = {
        "extra": "allow"  
    }


class AlarmResultRequest(BaseModel):
    """
    Table 1-21: 智能分析任务结果上报 Payload。
    """
    captureTime: str = Field(..., description="抓拍时间，格式yyyy-MM-dd HH:mm:ss")
    captureBase64: str | None = Field(default=None, description="抓拍全景图Base64编码")
    detectBase64: str | None = Field(default=None, description="识别小图Base64编码")
    desc: str | None = Field(default=None, description="告警描述")
    attributes: AlarmAttributes = Field(default_factory=AlarmAttributes, description="结构化属性")



# ---------------------------------------------------------------------------
# Algorithm Binding API Models (Table 1-19, 1-20)
# ---------------------------------------------------------------------------



class TaskConfig(BaseModel):
    threshold: int | None = None
    holddownTime: int | None = None
    stateReportTime: int | None = None


class BindRequest(BaseModel):
    sourceSystem: str = Field(..., description="来源系统，如 SPY/HYSP")
    bindId: str = Field(..., max_length=32, description="算法绑定请求唯一标识")
    cameraId: str = Field(..., max_length=32, description="摄像机id")
    algorithmList: list[str] = Field(..., description="智能分析算法产品编码列表")
    # Using 'configuation' to match provided JSON key in table 1-19
    configuation: TaskConfig | None = None
    liveUrl: str = Field(..., max_length=256, description="查询实时视频流api地址")
    report: ReportConfig
    beginTime: str | None = None
    endTime: str | None = None
    extendParamJson: str | dict | None = None


class BindResponse(BaseModel):
    resultCode: int = 0
    resultDesc: str = "任务下发成功"



class TaskStatus(BaseModel):
    stream_id: str
    stream_name: str
    status: str  # "running", "error", "offline"
    labels: list[str] = Field(default_factory=list)
    latest_frame_ts: int | None = None


class TaskDetail(BaseModel):
    task: TaskStatus
    recent_results: list[FrameResult] = Field(default_factory=list)

# ---------------------------------------------------------------------------
# Internal Task Models
# ---------------------------------------------------------------------------

class Task(BaseModel):
    """Internal task passed from Monitor -> Detection."""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    bindId: str
    cameraId: str
    # rtsp_url: str
    timestamp_ms: int
    image_data: bytes
    target_labels: list[str]
    # 新增：让每个 Task 携带上报地址
    status_report_url: str | None = None
    result_report_url: str | None = None


class HistoryRecord(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ms: int
    stream_id: str
    stream_name: str
    detections: list[DetectionResult]
    image_url: str = ""

class UnbindRequest(BaseModel):
    sourceSystem: str
    bindId: str = Field(...)
    cameraId: str = Field(...)
    algorithmList: list[str]
