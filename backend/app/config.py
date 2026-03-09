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

class FrameExtractionConfig(BaseModel):
    fps: float | None = None
    interval_s: float | None = None


class StreamConfig(BaseModel):
    id: str
    name: str
    rtsp_url: str
    enabled: bool = True
    frame_extraction: FrameExtractionConfig = FrameExtractionConfig()
    labels: list[str] = Field(default_factory=list)


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


class GrpcConfig(BaseModel):
    detection_host: str = "localhost"
    detection_port: int = 50051


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class AppConfig(BaseModel):
    streams: list[StreamConfig] = Field(default_factory=list)
    detection: DetectionConfig = DetectionConfig()
    alarm: AlarmConfig = AlarmConfig()
    grpc: GrpcConfig = GrpcConfig()
    server: ServerConfig = ServerConfig()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = os.environ.get(
    "LIGHTMONITOR_CONFIG",
    str(Path(__file__).resolve().parent.parent.parent.parent / "config" / "config.yaml"),
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
