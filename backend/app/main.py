"""LightMonitor – FastAPI application entry-point."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_config
from app.services.alarm import AlarmService
from app.services.detection import DetectionService
from app.services.monitor import MonitorService
from app.api.v1.tasks import init_router, router as tasks_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Global service references
_monitor: MonitorService | None = None
_detection: DetectionService | None = None
_alarm: AlarmService | None = None


def _configure_timed_rotating_handler(config) -> None:
    """Attach a TimedRotatingFileHandler to the root logger."""
    log_cfg = config.logging
    log_path = log_cfg.jsonl_path
    # Place the application log alongside the JSONL detections file
    log_dir = os.path.dirname(log_path) or "."
    os.makedirs(log_dir, exist_ok=True)
    app_log_path = os.path.join(log_dir, "app.log")

    handler = TimedRotatingFileHandler(
        app_log_path,
        when=log_cfg.rotate_when,
        backupCount=log_cfg.backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)
    logger.info("TimedRotatingFileHandler configured: %s", app_log_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor, _detection, _alarm

    config = get_config()
    logger.info("Loaded configuration with %d stream(s)", len(config.streams))

    _configure_timed_rotating_handler(config)

    # Create the shared async queue with backpressure limit
    queue: asyncio.Queue = asyncio.Queue(maxsize=config.queue.maxsize)

    _alarm = AlarmService(config.alarm)
    _detection = DetectionService(config, _alarm, queue)
    _monitor = MonitorService(config, queue)

    init_router(_monitor, _detection, jsonl_path=config.logging.jsonl_path)

    # Start detection consumer and all monitor stream tasks
    await _detection.start()
    await _monitor.start_all()
    logger.info("All services started")

    yield

    # Shutdown
    await _monitor.stop_all()
    await _detection.close()
    await _alarm.close()
    logger.info("All services stopped")


app = FastAPI(
    title="LightMonitor",
    description="Video Stream AI Detection Platform",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
