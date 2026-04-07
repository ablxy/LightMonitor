#!/usr/bin/env python3
"""
test_e2e_pipeline.py

全流程端到端集成测试，覆盖完整业务链路：

  bind → RTSP 帧读取 → AI 推理(mock) → 快照存盘
       → SQLite 入库 → 告警推送 → API 查询 → 快照接口 → unbind

依赖：
  - ffmpeg (brew install ffmpeg)
  - 本地测试视频文件（见 REAL_VIDEO_PATH）
  - aiosqlite, pytest-asyncio, opencv-python-headless, httpx, pytest
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch
import sqlite3 as _sqlite3
import json as _json

import pytest

REAL_VIDEO_PATH = "/Users/iecon/Desktop/code/LightMonitor/5086645-uhd_3840_2160_30fps.mp4"
RTSP_PORT = 8554
MOCK_SERVER_PORT = 18767
BIND_ID = "e2e_stream_001"
CAMERA_ID = "E2E_Camera_001"
ALGO_LABEL = "person_detect"  # 需与绑定时 algorithmList 一致


# ===========================================================
# 辅助
# ===========================================================

def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _sign(username: str, password: str, url: str) -> str:
    return _md5(f"{username}{password}{url}")


# ===========================================================
# 外部基础设施 fixtures
# ===========================================================

@pytest.fixture(scope="module")
def real_video():
    path = Path(REAL_VIDEO_PATH)
    if not path.exists():
        pytest.skip(f"测试视频不存在: {REAL_VIDEO_PATH}")
    return str(path)


@pytest.fixture(scope="module")
def rtsp_server(real_video: str):
    """用 ffmpeg 暴露 RTSP 服务端，循环推流。"""
    if not shutil.which("ffmpeg"):
        pytest.skip("未找到 ffmpeg，brew install ffmpeg")

    rtsp_url = f"rtsp://127.0.0.1:{RTSP_PORT}/e2e"
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-re", "-stream_loop", "-1", "-i", real_video,
            "-vcodec", "libx264", "-preset", "ultrafast",
            "-tune", "zerolatency", "-g", "10",
            "-rtsp_transport", "tcp",
            "-f", "rtsp", "-rtsp_flags", "listen",
            rtsp_url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(2.0)
    if proc.poll() is not None:
        pytest.skip("ffmpeg RTSP 服务器启动失败")

    yield rtsp_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def external_servers(rtsp_server: str):
    """
    多功能 mock HTTP 服务器，统一处理：
      GET  /live    → 返回 RTSP URL (liveUrl 响应)
      POST /status  → 接收状态上报
      POST /result  → 接收告警结果上报
    """
    received_alarms: list[dict] = []
    received_statuses: list[dict] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/live":
                body = json.dumps({"resultCode": 0, "url": rtsp_server}).encode()
                self._send(200, body)
            else:
                self._send(404, b"Not found")

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw)
            except Exception:
                data = {}

            if self.path == "/result":
                received_alarms.append(data)
                self._send(200, json.dumps({"resultCode": 0}).encode())
            elif self.path == "/status":
                received_statuses.append(data)
                self._send(200, json.dumps({"resultCode": 0}).encode())
            else:
                self._send(404, b"Not found")

        def _send(self, code: int, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", MOCK_SERVER_PORT), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    base = f"http://127.0.0.1:{MOCK_SERVER_PORT}"
    yield {
        "live_url":       f"{base}/live",
        "status_url":     f"{base}/status",
        "result_url":     f"{base}/result",
        "received_alarms":   received_alarms,
        "received_statuses": received_statuses,
    }

    srv.shutdown()


# ===========================================================
# 临时存储 + 配置 fixtures
# ===========================================================

@pytest.fixture(scope="module")
def temp_dirs(tmp_path_factory):
    base = tmp_path_factory.mktemp("e2e")
    return {
        "db":        str(base / "test.db"),
        "snapshots": str(base / "snapshots"),
        "logs":      str(base / "logs"),
    }


@pytest.fixture(scope="module")
def temp_config(temp_dirs, tmp_path_factory):
    """写入临时 config.yaml，指向临时 DB 和快照目录。"""
    cfg_dir = tmp_path_factory.mktemp("cfg")
    cfg_path = cfg_dir / "config.yaml"
    cfg_path.write_text(textwrap.dedent(f"""
        detection:
          model_url: ""
          confidence_threshold: 0.5
        queue:
          maxsize: 50
        storage:
          db_path: "{temp_dirs['db']}"
          snapshots_dir: "{temp_dirs['snapshots']}"
        logging:
          jsonl_path: "{temp_dirs['logs']}/detections.jsonl"
          rotate_when: midnight
          backup_count: 7
        api_auth:
          username: "e2eadmin"
          password: "e2epassword"
    """))
    return str(cfg_path)


# ===========================================================
# 完整 FastAPI app fixture
# ===========================================================

_MOCK_DETECTIONS = [
    {
        "label": ALGO_LABEL,
        "confidence": 0.92,
        "bbox": {"x_min": 50.0, "y_min": 80.0, "x_max": 300.0, "y_max": 700.0},
    }
]


@pytest.fixture(scope="module")
def app_client(external_servers, temp_config):
    import app.config as config_module
    import app.services.detection  # noqa: F401  ← 新增
    import app.services.monitor    # noqa: F401  ← 新增

    config_module.get_config.cache_clear()

    with (
        patch("app.config._DEFAULT_CONFIG_PATH", temp_config),
        patch(
            "app.services.detection.DetectionService._call_model",
            new=AsyncMock(return_value=_MOCK_DETECTIONS),
        ),
        patch("app.services.monitor.StreamTask.upload_status", new=AsyncMock()),
    ):
        config_module.get_config.cache_clear()

        from app.main import app
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    config_module.get_config.cache_clear()


# ===========================================================
# 全流程测试
# ===========================================================

def test_full_pipeline(app_client, external_servers, temp_dirs):
    """
    全流程验证：

    1. POST /bind     → 注册流任务
    2. 驱动 task.start() → RTSP 帧读取 → 推理 → 快照存盘 + SQLite 入库 + 告警推送
    3. GET /api/v1/history        → SQLite 记录可查
    4. GET /api/v1/snapshots/...  → 快照文件可访问
    5. mock 服务器收到告警 payload
    6. POST /unbind   → 任务移除
    """
    from app.main import _monitor, _database

    BIND_PATH   = "/ai-video-analysis/ai/v1/api/algorithm/bind"
    UNBIND_PATH = "/ai-video-analysis/ai/v1/api/algorithm/unbind"
    username, password = "e2eadmin", "e2epassword"

    # -------------------------------------------------------
    # Step 1: POST /bind
    # -------------------------------------------------------
    sign = _sign(username, password, f"http://testserver{BIND_PATH}")
    bind_resp = app_client.post(
        BIND_PATH,
        json={
            "sourceSystem": "E2E_TEST",
            "bindId":        BIND_ID,
            "cameraId":      CAMERA_ID,
            "algorithmList": [ALGO_LABEL],
            "liveUrl":       external_servers["live_url"],
            "report": {
                "statusReportUrl": external_servers["status_url"],
                "resultReportUrl": external_servers["result_url"],
            },
        },
        headers={"X-Sign": sign},
    )
    assert bind_resp.status_code == 200
    assert bind_resp.json()["resultCode"] == 0, f"/bind 失败: {bind_resp.text}"

    # -------------------------------------------------------
    # Step 2: 确认任务注册
    # -------------------------------------------------------
    assert _monitor is not None
    task = _monitor.tasks.get(BIND_ID)
    assert task is not None, f"MonitorService 中未找到 bindId={BIND_ID}"

    # -------------------------------------------------------
    # Step 3: /bind 已自动触发 task.start()，等待管道产出 SQLite 记录
    # -------------------------------------------------------
    db_path = temp_dirs["db"]
    rows = []
    deadline = time.time() + 30.0
    while time.time() < deadline:
        try:
            with _sqlite3.connect(db_path) as _c:
                _c.row_factory = _sqlite3.Row
                _rows = _c.execute(
                    "SELECT * FROM history WHERE stream_id=? LIMIT 1",
                    (BIND_ID,),
                ).fetchall()
                if _rows:
                    rows = list(_rows)
                    break
        except Exception:
            pass
        time.sleep(0.5)

    # -------------------------------------------------------
    # Step 4: 断言 SQLite 已入库
    # -------------------------------------------------------
    rows = []
    deadline = time.time() + 25.0
    while time.time() < deadline:
        try:
            with _sqlite3.connect(db_path) as _c:
                _c.row_factory = _sqlite3.Row
                _rows = _c.execute(
                    "SELECT task_id, timestamp_ms, stream_id, stream_name, detections, image_url "
                    "FROM history WHERE stream_id=? ORDER BY timestamp_ms DESC LIMIT 10",
                    (BIND_ID,),
                ).fetchall()
                if _rows:
                    rows = list(_rows)
                    break
        except Exception:
            pass
        time.sleep(0.5)

    # Step 4: 断言 SQLite 已入库
    assert len(rows) >= 1, "SQLite history 表中无记录，管道未正确运行"
    row = rows[0]
    assert row["stream_id"] == BIND_ID
    assert row["stream_name"] == CAMERA_ID
    detections = _json.loads(row["detections"])
    assert any(d["label"] == ALGO_LABEL for d in detections)
    image_url: str = row["image_url"]
    assert image_url.startswith(f"/api/v1/snapshots/{BIND_ID}/"), \
        f"image_url 格式异常: {image_url}"

    # -------------------------------------------------------
    # Step 5: 断言快照文件存在于磁盘
    # -------------------------------------------------------
    # image_url = /api/v1/snapshots/{stream_id}/{filename}
    filename = Path(image_url).name
    snapshot_path = Path(temp_dirs["snapshots"]) / BIND_ID / filename
    assert snapshot_path.exists(), f"快照文件不存在: {snapshot_path}"
    assert snapshot_path.stat().st_size > 500, "快照文件大小异常（疑似空文件）"

    # -------------------------------------------------------
    # Step 6: GET /api/v1/history 接口验证
    # -------------------------------------------------------
    history_resp = app_client.get(
        f"/api/v1/history?stream_id={BIND_ID}&limit=10"
    )
    assert history_resp.status_code == 200
    history_data = history_resp.json()
    assert len(history_data) >= 1, "/api/v1/history 返回空"
    assert history_data[0]["stream_id"] == BIND_ID

    # -------------------------------------------------------
    # Step 7: GET /api/v1/snapshots/{stream_id}/{filename} 接口验证
    # -------------------------------------------------------
    snapshot_api_resp = app_client.get(f"/api/v1/snapshots/{BIND_ID}/{filename}")
    assert snapshot_api_resp.status_code == 200
    assert snapshot_api_resp.headers["content-type"] == "image/jpeg"
    assert len(snapshot_api_resp.content) > 500

    # -------------------------------------------------------
    # Step 8: 告警推送到达 mock 服务器
    # -------------------------------------------------------
    assert len(external_servers["received_alarms"]) >= 1, \
        "告警 mock 服务器未收到任何 POST 请求，AlarmService 未推送"
    alarm = external_servers["received_alarms"][0]
    assert alarm.get("bindId") == BIND_ID
    assert alarm.get("algorithmType") == ALGO_LABEL

    # -------------------------------------------------------
    # Step 9: POST /unbind 并验证任务移除
    # -------------------------------------------------------
    unbind_sign = _sign(username, password, f"http://testserver{UNBIND_PATH}")
    unbind_resp = app_client.post(
        UNBIND_PATH,
        json={"bindId": BIND_ID, "cameraId": CAMERA_ID, "algorithmList": [ALGO_LABEL], "sourceSystem": "E2E_TEST"},
        headers={"X-Sign": unbind_sign},
    )
    assert unbind_resp.status_code == 200
    assert unbind_resp.json()["resultCode"] == 0, f"/unbind 失败: {unbind_resp.text}"
    assert BIND_ID not in _monitor.tasks, "任务未从 MonitorService 中移除"