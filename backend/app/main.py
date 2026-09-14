"""LightMonitor – FastAPI application entry-point."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.algo_bind import init_binding_router
from app.api.v1.algo_bind import router as algo_bind_router
from app.api.v1.tasks import init_router
from app.api.v1.tasks import router as tasks_router
from app.config import get_config
from app.errors import error_payload, install_exception_handlers
from app.models import Task
from app.observability import configure_logging, reset_request_id, set_request_id
from app.services.alarm import AlarmService
from app.services.database import DatabaseService
from app.services.detection import DetectionService
from app.services.monitor import MonitorService

logger = logging.getLogger(__name__)


# Global service references
_monitor: MonitorService | None = None
_detection: DetectionService | None = None
_alarm: AlarmService | None = None
_database: DatabaseService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor, _detection, _alarm, _database

    _monitor = None
    _detection = None
    _alarm = None
    _database = None

    config = get_config()
    configure_logging(config.logging)
    logger.info(
        "configuration loaded",
        extra={
            "event": "app.configuration.loaded",
            "stream_count": len(config.streams),
        },
    )

    # Create the shared async queue with backpressure limit
    queue: asyncio.Queue[Task] = asyncio.Queue(maxsize=config.queue.maxsize)

    _alarm = AlarmService()
    _database = DatabaseService(config.storage.db_path)
    _detection = DetectionService(
        config,
        _alarm,
        queue,
        _database,
        num_workers=config.queue.workers,
    )
    try:
        await _database.start()
        _monitor = MonitorService(config, queue)

        init_router(
            _monitor,
            _detection,
            db_service=_database,
            snapshots_dir=config.storage.snapshots_dir,
        )
        init_binding_router(_monitor)

        await _detection.start()
        await _monitor.start_all()
        logger.info("all services started", extra={"event": "app.started"})
        yield
    finally:
        if _monitor is not None:
            await _monitor.stop_all()
        if _detection is not None:
            await _detection.close()
        if _database is not None:
            await _database.close()
        if _alarm is not None:
            await _alarm.close()
        logger.info("all services stopped", extra={"event": "app.stopped"})
        _monitor = None
        _detection = None
        _alarm = None
        _database = None


app = FastAPI(
    title="LightMonitor",
    description="Video Stream AI Detection Platform",
    version="2.0.0",
    lifespan=lifespan,
)

install_exception_handlers(app)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@app.middleware("http")
async def request_context(request: Request, call_next):
    supplied_id = request.headers.get("X-Request-ID", "")
    request_id = (
        supplied_id if _REQUEST_ID_PATTERN.fullmatch(supplied_id) else uuid.uuid4().hex
    )
    token = set_request_id(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "unhandled request error",
                extra={
                    "event": "api.request.unhandled_error",
                    "method": request.method,
                    "path": request.url.path,
                    "error_type": type(exc).__name__,
                },
            )
            response = JSONResponse(
                status_code=500,
                content=error_payload("INTERNAL_ERROR", "服务内部错误"),
            )
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request completed",
            extra={
                "event": "api.request.completed",
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response
    finally:
        reset_request_id(token)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(algo_bind_router)
app.include_router(tasks_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
