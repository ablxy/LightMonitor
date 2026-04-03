"""Configuration loader for LightMonitor.

Reads the YAML configuration file and exposes strongly-typed Pydantic models
so the rest of the application never deals with raw dicts.
"""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

import yaml
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic configuration models
# ---------------------------------------------------------------------------

class ReportConfig(BaseModel):
    # status_report_url: str = "http://1.1.1.1:8080/report/task_status?platformId=0001"
    # result_report_url: str = "http://1.1.1.1:8080/report/task_result?platformId=0001"
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
    report: ReportConfig



class AuthConfig(BaseModel):
    type: str = "none"
    token: str = ""


class DetectionConfig(BaseModel):
    model_url: str = ""
    auth: AuthConfig = AuthConfig()
    confidence_threshold: float = 0.5


class AlarmConfig(BaseModel):
    enabled: bool = False
    webhook_url: str = ""
    auth: AuthConfig = AuthConfig()


class QueueConfig(BaseModel):
    maxsize: int = 100


class RustFSConfig(BaseModel):
    endpoint: str = "localhost:9000"
    access_key: str = "rustfsadmin"
    secret_key: str = "rustfssecret"
    bucket: str = "lightmonitor"
    secure: bool = False


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
    rustfs: RustFSConfig = RustFSConfig()
    logging: LoggingConfig = LoggingConfig()
    api_auth: ApiAuthConfig = ApiAuthConfig()
    report: ReportConfig = ReportConfig()


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
