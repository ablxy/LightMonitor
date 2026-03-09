"""Detection Service – gRPC server that receives frames and runs AI inference."""

from __future__ import annotations

import asyncio
import base64
import collections
import logging
import time
from typing import TYPE_CHECKING

import grpc
import httpx
from grpc import aio as grpc_aio

from app.models import BoundingBox, DetectionResult, FrameResult

if TYPE_CHECKING:
    from app.config import AppConfig
    from app.services.alarm import AlarmService

logger = logging.getLogger(__name__)

# Keep the N most recent results per stream for the API to serve.
MAX_RESULTS_PER_STREAM = 50


class DetectionService:
    """Manages frame processing, AI inference invocation, and result storage."""

    def __init__(self, config: AppConfig, alarm_service: AlarmService) -> None:
        self._config = config
        self._alarm = alarm_service
        self._http = httpx.AsyncClient(timeout=30.0)
        # stream_id -> deque of FrameResult
        self.results: dict[str, collections.deque[FrameResult]] = {}
        for s in config.streams:
            self.results[s.id] = collections.deque(maxlen=MAX_RESULTS_PER_STREAM)

    # ------------------------------------------------------------------
    # AI model invocation
    # ------------------------------------------------------------------

    async def _call_model(self, image_bytes: bytes) -> list[dict]:
        """Call the external AI model API and return raw detection dicts."""
        cfg = self._config.detection
        if not cfg.model_url:
            return []

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if cfg.auth.type == "bearer" and cfg.auth.token:
            headers["Authorization"] = f"Bearer {cfg.auth.token}"
        elif cfg.auth.type == "api_key" and cfg.auth.token:
            headers["X-API-Key"] = cfg.auth.token

        b64 = base64.b64encode(image_bytes).decode()
        payload = {"image": b64}

        try:
            resp = await self._http.post(cfg.model_url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            # Expect {"detections": [{label, confidence, bbox: {x_min,y_min,x_max,y_max}}]}
            return data.get("detections", [])
        except httpx.HTTPError:
            logger.exception("Model call failed")
            return []

    # ------------------------------------------------------------------
    # Frame processing pipeline
    # ------------------------------------------------------------------

    async def process_frame(
        self,
        stream_id: str,
        stream_name: str,
        image_data: bytes,
        timestamp_ms: int,
        target_labels: list[str],
    ) -> FrameResult:
        """Run inference, filter by labels & threshold, and store result."""
        raw_dets = await self._call_model(image_data)

        threshold = self._config.detection.confidence_threshold
        detections: list[DetectionResult] = []
        for d in raw_dets:
            conf = float(d.get("confidence", 0))
            label = d.get("label", "")
            if conf < threshold:
                continue
            bbox = None
            if "bbox" in d and d["bbox"]:
                bbox = BoundingBox(**d["bbox"])
            detections.append(DetectionResult(label=label, confidence=conf, bbox=bbox))

        # Determine if any detection matches a target label -> alarm
        alarmed = any(det.label in target_labels for det in detections)

        b64_image = base64.b64encode(image_data).decode()
        frame_result = FrameResult(
            stream_id=stream_id,
            stream_name=stream_name,
            timestamp_ms=timestamp_ms,
            detections=detections,
            alarmed=alarmed,
            image_base64=b64_image,
        )

        # Store
        if stream_id not in self.results:
            self.results[stream_id] = collections.deque(maxlen=MAX_RESULTS_PER_STREAM)
        self.results[stream_id].appendleft(frame_result)

        # Push alarm if needed
        if alarmed:
            asyncio.create_task(self._alarm.push(frame_result))

        return frame_result

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_recent_results(self, stream_id: str, limit: int = 20) -> list[FrameResult]:
        dq = self.results.get(stream_id)
        if not dq:
            return []
        return list(dq)[:limit]


# ======================================================================
# gRPC servicer implementation
# ======================================================================

# Import generated stubs lazily so the module is importable even when
# the proto files haven't been compiled yet (e.g. during config-only tests).
_pb2: object | None = None
_pb2_grpc: object | None = None


def _ensure_grpc_stubs():  # noqa: ANN202
    global _pb2, _pb2_grpc
    if _pb2 is None:
        from app.grpc_generated import frame_stream_pb2, frame_stream_pb2_grpc
        _pb2 = frame_stream_pb2
        _pb2_grpc = frame_stream_pb2_grpc


class FrameStreamServicer:
    """gRPC servicer that delegates to DetectionService."""

    def __init__(self, detection_service: DetectionService) -> None:
        self._det = detection_service

    async def PushFrame(self, request, context):  # noqa: N802
        _ensure_grpc_stubs()
        result = await self._det.process_frame(
            stream_id=request.stream_id,
            stream_name=request.stream_name,
            image_data=bytes(request.image_data),
            timestamp_ms=request.timestamp_ms,
            target_labels=list(request.target_labels),
        )
        det_msgs = []
        for d in result.detections:
            bbox_msg = None
            if d.bbox:
                bbox_msg = _pb2.BoundingBox(
                    x_min=d.bbox.x_min,
                    y_min=d.bbox.y_min,
                    x_max=d.bbox.x_max,
                    y_max=d.bbox.y_max,
                )
            det_msgs.append(
                _pb2.Detection(
                    label=d.label,
                    confidence=d.confidence,
                    bbox=bbox_msg,
                )
            )
        return _pb2.PushResponse(
            success=True,
            message="OK",
            detections=det_msgs,
        )


async def start_grpc_server(
    detection_service: DetectionService,
    host: str = "0.0.0.0",
    port: int = 50051,
) -> grpc_aio.Server:
    """Start the gRPC server for the Detection Service."""
    _ensure_grpc_stubs()
    server = grpc_aio.server()
    _pb2_grpc.add_FrameStreamServiceServicer_to_server(
        FrameStreamServicer(detection_service), server
    )
    listen_addr = f"{host}:{port}"
    server.add_insecure_port(listen_addr)
    await server.start()
    logger.info("gRPC Detection server listening on %s", listen_addr)
    return server
