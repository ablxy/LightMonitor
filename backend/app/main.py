"""LightMonitor – FastAPI application entry-point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_config
from app.services.alarm import AlarmService
from app.services.detection import DetectionService, start_grpc_server
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
_grpc_server = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor, _detection, _alarm, _grpc_server

    config = get_config()
    logger.info("Loaded configuration with %d stream(s)", len(config.streams))

    _alarm = AlarmService(config.alarm)
    _detection = DetectionService(config, _alarm)
    _monitor = MonitorService(config)

    init_router(_monitor, _detection)

    # Start gRPC detection server
    _grpc_server = await start_grpc_server(
        _detection,
        host=config.grpc.detection_host,
        port=config.grpc.detection_port,
    )

    # Start all monitor stream tasks
    await _monitor.start_all()
    logger.info("All services started")

    yield

    # Shutdown
    await _monitor.stop_all()
    if _grpc_server:
        await _grpc_server.stop(grace=5)
    await _detection.close()
    await _alarm.close()
    logger.info("All services stopped")


app = FastAPI(
    title="LightMonitor",
    description="Video Stream AI Detection Platform",
    version="0.1.0",
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
