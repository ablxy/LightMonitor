"""Pydantic response models used across the API and services."""

from __future__ import annotations

from pydantic import BaseModel, Field


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


class TaskStatus(BaseModel):
    stream_id: str
    stream_name: str
    status: str  # "running", "error", "offline"
    labels: list[str] = Field(default_factory=list)
    latest_frame_ts: int | None = None


class TaskDetail(BaseModel):
    task: TaskStatus
    recent_results: list[FrameResult] = Field(default_factory=list)
