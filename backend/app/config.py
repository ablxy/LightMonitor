"""Configuration loader for LightMonitor.

Reads the YAML configuration file and exposes strongly-typed Pydantic models
so the rest of the application never deals with raw dicts.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Pydantic configuration models
# ---------------------------------------------------------------------------


class ReportConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status_report_url: str | None = Field(
        default=None, alias="statusReportUrl", description="任务状态上报URL"
    )
    result_report_url: str | None = Field(
        default=None, alias="resultReportUrl", description="任务结果上报URL"
    )


class FrameExtractionConfig(BaseModel):
    fps: float | None = None
    interval_s: float | None = None


class StreamConfig(BaseModel):
    bindId: str = Field(validation_alias=AliasChoices("bindId", "id"))
    cameraId: str = Field(validation_alias=AliasChoices("cameraId", "name"))
    live_url: str = Field(
        validation_alias=AliasChoices("live_url", "rtsp_url", "liveUrl")
    )
    enabled: bool = True
    frame_extraction: FrameExtractionConfig = Field(
        default_factory=FrameExtractionConfig
    )
    labels: list[str] = Field(default_factory=list)
    report: ReportConfig = Field(default_factory=ReportConfig)
    confidence_threshold: float | None = Field(default=None, ge=0, le=1)


class AuthConfig(BaseModel):
    type: str = "none"
    token: str = ""


class VLMConfig(BaseModel):
    """VLM-specific configuration (OpenAI-compatible protocol)."""

    system_prompt: str = ""
    prompt: str = ""


class DetectionConfig(BaseModel):
    model_url: str = ""
    auth: AuthConfig = Field(default_factory=AuthConfig)
    confidence_threshold: float = Field(default=0.5, ge=0, le=1)
    model_type: str = "yolo"  # "yolo" | "vlm"
    model_name: str = ""  # model name sent in the request body
    vlm: VLMConfig = Field(default_factory=VLMConfig)


class QueueConfig(BaseModel):
    maxsize: int = Field(default=100, ge=1)
    workers: int = Field(default=4, ge=1)
    max_frame_age_s: float = Field(default=5.0, gt=0)
    shutdown_timeout_s: float = Field(default=10.0, gt=0)
    alarm_maxsize: int = Field(default=100, ge=1)
    alarm_workers: int = Field(default=2, ge=1)


class StorageConfig(BaseModel):
    db_path: str = "data/lightmonitor.db"
    snapshots_dir: str = "data/snapshots"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"
    console_enabled: bool = True
    file_enabled: bool = True
    file_path: str | None = None
    # Backward-compatible input. Detection history now lives in SQLite.
    jsonl_path: str = "logs/detections.jsonl"
    rotate_when: str = "midnight"
    backup_count: int = 7

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("logging.level 无效")
        return normalized

    @field_validator("format")
    @classmethod
    def validate_format(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"json", "text"}:
            raise ValueError("logging.format 必须是 json 或 text")
        return normalized

    @property
    def resolved_file_path(self) -> str:
        if self.file_path:
            return self.file_path
        return str(Path(self.jsonl_path).with_name("app.log"))


class ApiAuthConfig(BaseModel):
    username: str = "maasadmin"
    password: str = "Maas@dj0086"


class AppConfig(BaseModel):
    streams: list[StreamConfig] = Field(default_factory=list)
    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    api_auth: ApiAuthConfig = Field(default_factory=ApiAuthConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = os.environ.get(
    "LIGHTMONITOR_CONFIG",
    str(Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"),
)


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
