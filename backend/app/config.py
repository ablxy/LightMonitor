"""Configuration loader for LightMonitor.

Reads the YAML configuration file and exposes strongly-typed Pydantic models
so the rest of the application never deals with raw dicts.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Pydantic configuration models
# ---------------------------------------------------------------------------

class ReportConfig(BaseModel):
    status_report_url: str|None = Field(default=None,alias="statusReportUrl", description="任务状态上报URL")
    result_report_url: str|None = Field(default=None,alias="resultReportUrl", description="任务结果上报URL")

    class Config:
            allow_population_by_field_name = True


class FrameExtractionConfig(BaseModel):
    fps: float | None = None
    interval_s: float | None = None


class StreamConfig(BaseModel):
    bindId: str
    cameraId: str
    live_url: str
    enabled: bool = True
    frame_extraction: FrameExtractionConfig = FrameExtractionConfig()
    labels: list[str] = Field(default_factory=list)
    report: ReportConfig = ReportConfig()

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data.setdefault("bindId", data.pop("id", None))
        data.setdefault("cameraId", data.pop("name", None))
        data.setdefault("live_url", data.pop("rtsp_url", None))
        return data



class AuthConfig(BaseModel):
    type: str = "none"
    token: str = ""


class VLMConfig(BaseModel):
    """VLM-specific configuration (OpenAI-compatible protocol)."""
    system_prompt: str = ""
    prompt: str = ""


class DetectionConfig(BaseModel):
    model_url: str = ""
    auth: AuthConfig = AuthConfig()
    confidence_threshold: float = 0.5
    model_type: str = "yolo"   # "yolo" | "vlm"
    model_name: str = ""       # model name sent in the request body
    vlm: VLMConfig = VLMConfig()


class AlarmConfig(BaseModel):
    enabled: bool = False
    webhook_url: str = ""
    auth: AuthConfig = AuthConfig()


class RustfsConfig(BaseModel):
    """Backward-compatible name used by the existing manual upload test."""

    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    bucket: str = "lightmonitor"
    secure: bool = False




class QueueConfig(BaseModel):
    maxsize: int = 100
    backend: str = "memory"
    redis_url: str = "redis://localhost:6379/0"
    task_queue: str = "lightmonitor:vlm_tasks"
    alarm_queue: str = "lightmonitor:alarm_save"
    result_ttl_seconds: int = 3600


class StorageConfig(BaseModel):
    db_path: str = "data/lightmonitor.db"
    snapshots_dir: str = "data/snapshots"


class MysqlConfig(BaseModel):
    """MySQL 5.7-compatible connection settings for the target architecture."""

    url: str = "mysql+pymysql://lightmonitor:lightmonitor@localhost:3306/lightmonitor"
    pool_size: int = 10
    max_overflow: int = 20
    pool_recycle_seconds: int = 1800


class MinioConfig(BaseModel):
    """S3-compatible object storage settings.

    ``endpoint`` deliberately has no scheme because the MinIO SDK expects a
    host:port value. The URL form remains available for generated links.
    """

    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    bucket: str = "lightmonitor"
    secure: bool = False
    presigned_url_expire_seconds: int = 3600


class WorkerConfig(BaseModel):
    producer_workers: int = 1
    consumer_workers: int = 1
    consumer_threads_per_worker: int = 4
    heartbeat_interval_seconds: int = 10
    max_retries: int = 3


class RagConfig(BaseModel):
    enabled: bool = False
    persist_directory: str = "data/chroma"
    collection_name: str = "lightmonitor_rules"
    top_k: int = 5


class TemporalConfig(BaseModel):
    window_seconds: float = 5.0
    frames_count: int = 8


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class LoggingConfig(BaseModel):
    jsonl_path: str = "logs/detections.jsonl"
    rotate_when: str = "midnight"
    backup_count: int = 7



class ApiAuthConfig(BaseModel):
    username: str = "maasadmin"
    password: str = "Maas@dj0086"


class AppConfig(BaseModel):
    streams: list[StreamConfig] = Field(default_factory=list)
    detection: DetectionConfig = DetectionConfig()
    alarm: AlarmConfig = AlarmConfig()
    queue: QueueConfig = QueueConfig()
    storage: StorageConfig = StorageConfig()
    logging: LoggingConfig = LoggingConfig()
    api_auth: ApiAuthConfig = ApiAuthConfig()
    report: ReportConfig = ReportConfig()
    mysql: MysqlConfig = MysqlConfig()
    minio: MinioConfig = MinioConfig()
    rustfs: RustfsConfig = RustfsConfig()
    workers: WorkerConfig = WorkerConfig()
    rag: RagConfig = RagConfig()
    temporal: TemporalConfig = TemporalConfig()
    server: ServerConfig = ServerConfig()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = os.environ.get(
    "LIGHTMONITOR_CONFIG",
    str(Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"),
)

print(f"Using configuration file: {_DEFAULT_CONFIG_PATH}")

def load_config(path: str | None = None) -> AppConfig:
    """Load and validate configuration from a YAML file."""
    config_path = path or _DEFAULT_CONFIG_PATH
    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return a cached singleton of the application configuration."""
    return load_config()
