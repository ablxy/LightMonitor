"""Pydantic response models used across the API and services."""

from __future__ import annotations

import uuid

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


class Task(BaseModel):
    """Internal task object passed through the async queue."""

    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    stream_id: str
    stream_name: str
    image_data: bytes
    timestamp_ms: int
    target_labels: list[str] = Field(default_factory=list)


class HistoryRecord(BaseModel):
    """Persisted detection event, stored as a JSONL line."""

    task_id: str
    timestamp_ms: int
    stream_id: str
    stream_name: str
    detections: list[DetectionResult] = Field(default_factory=list)
    image_url: str = ""
