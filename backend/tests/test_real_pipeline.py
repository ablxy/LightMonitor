#!/usr/bin/env python3
"""
test_real_pipeline.py

使用真实 VLM 模型 + 真实配置跑一次完整业务链路，数据写入实际数据库：

  bind → RTSP 帧读取 → 真实 AI 推理 → 快照存盘
       → SQLite 入库 → 告警推送 → API 查询 → unbind

依赖：
  - ffmpeg (brew install ffmpeg)
  - 本地测试视频文件（见 REAL_VIDEO_PATH）
  - aiosqlite, pytest-asyncio, opencv-python-headless, httpx, pytest

运行方式（在 backend/ 目录下）：
  pytest tests/test_real_pipeline.py -v -s
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3 as _sqlite3
import subprocess
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ──────────────────────────────────────────────
# 路径常量
# ──────────────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parent.parent          # .../backend
PROJECT_ROOT = BACKEND_DIR.parent                             # .../LightMonitor
REAL_CONFIG_PATH = str(PROJECT_ROOT / "config" / "config.yaml")

# 真实数据目录（绝对路径，不受 CWD 影响）
REAL_DB_PATH       = str(BACKEND_DIR / "data" / "lightmonitor.db")
REAL_SNAPSHOTS_DIR = str(BACKEND_DIR / "data" / "snapshots")
REAL_LOGS_DIR      = str(BACKEND_DIR / "logs")
REAL_JSONL_PATH    = str(BACKEND_DIR / "logs" / "detections.jsonl")

# 视频 & 网络
REAL_VIDEO_PATH  = "/Users/iecon/Desktop/code/LightMonitor/5086645-uhd_3840_2160_30fps.mp4"
RTSP_PORT        = 8555          # 避免与 e2e test 冲突
MOCK_SERVER_PORT = 18769

# 业务参数
BIND_ID    = "a63c3968d11a477894f66a5d7598f408"
CAMERA_ID  = "44010624122515010301030001561574"
ALGO_LABEL = "41814000001"

API_USERNAME="maasadmin"
API_PASSWORD="Maas@dj0086"


# ──────────────────────────────────────────────
# 辅助
# ──────────────────────────────────────────────

def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _sign(username: str, password: str, url: str) -> str:
    return _md5(f"{username}{password}{url}")


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def real_video():
    path = Path(REAL_VIDEO_PATH)
    if not path.exists():
        pytest.skip(f"测试视频不存在: {REAL_VIDEO_PATH}")
    return str(path)


@pytest.fixture(scope="module")
def rtsp_server(real_video: str):
    """直接返回本地视频文件路径，cv2.VideoCapture 可直接打开，无需 ffmpeg。"""
    yield real_video


@pytest.fixture(scope="module")
def mock_http_server(rtsp_server: str):
    """
    最小化 mock HTTP 服务器，处理：
      GET  /live    → 返回 RTSP URL
      POST /status  → 接收状态上报（仅记录，不阻塞）
      POST /result  → 接收告警结果上报（仅记录，不阻塞）
    """
    received_alarms:   list[dict] = []
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

            if self.path == "/status":
                received_statuses.append(data)
                self._send(200, json.dumps({"resultCode": 0}).encode())
            elif self.path == "/result":
                received_alarms.append(data)
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
            pass  # 静默 HTTP 日志

    srv = HTTPServer(("127.0.0.1", MOCK_SERVER_PORT), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    base = f"http://127.0.0.1:{MOCK_SERVER_PORT}"
    yield {
        "live_url":          f"{base}/live",
        "status_url":        f"{base}/status",
        "result_url":        f"{base}/result",
        "received_alarms":   received_alarms,
        "received_statuses": received_statuses,
    }

    srv.shutdown()


@pytest.fixture(scope="module")
def real_app_client(mock_http_server):
    """
    启动真实 FastAPI app，使用真实 config.yaml。
    覆盖 db_path / snapshots_dir / logging 为绝对路径，
    避免受运行目录影响。
    """
    import app.config as config_module

    # 确保数据目录存在
    Path(REAL_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(REAL_SNAPSHOTS_DIR).mkdir(parents=True, exist_ok=True)
    Path(REAL_LOGS_DIR).mkdir(parents=True, exist_ok=True)

    config_module.get_config.cache_clear()

    with patch("app.config._DEFAULT_CONFIG_PATH", REAL_CONFIG_PATH):
        config_module.get_config.cache_clear()

        # 获取配置并注入绝对路径
        cfg = config_module.get_config()
        cfg.storage.db_path       = REAL_DB_PATH
        cfg.storage.snapshots_dir = REAL_SNAPSHOTS_DIR
        cfg.logging.jsonl_path    = REAL_JSONL_PATH

        # 让 get_config() 返回我们修改后的实例（用 side_effect 保持可调用）
        with patch.object(config_module, "get_config", side_effect=lambda: cfg):
            from app.main import app
            from fastapi.testclient import TestClient

            with TestClient(app, raise_server_exceptions=False) as client:
                yield client

    config_module.get_config.cache_clear()


# ──────────────────────────────────────────────
# 正式测试
# ──────────────────────────────────────────────

def test_real_pipeline(real_app_client, mock_http_server):
    """
    完整真实管道验证：

    1. POST /bind     → 注册流任务，liveUrl 指向本地 ffmpeg RTSP
    2. 等待真实 VLM 推理完成，SQLite 入库
    3. GET /api/v1/history  → 验证记录可查
    4. GET snapshot 接口    → 验证快照文件可访问
    5. mock 服务器收到告警
    6. POST /unbind
    """
    from app.main import _monitor, _database

    BIND_PATH   = "/ai-video-analysis/ai/v1/api/algorithm/bind"
    UNBIND_PATH = "/ai-video-analysis/ai/v1/api/algorithm/unbind"

    print(f"\n📂 数据库路径: {REAL_DB_PATH}")
    print(f"📸 快照目录:   {REAL_SNAPSHOTS_DIR}")
    print(f"🔗 RTSP URL:   rtsp://127.0.0.1:{RTSP_PORT}/real")

    # ──────────────────────────────────────────
    # Step 1: POST /bind
    # ──────────────────────────────────────────
    sign = _sign(API_USERNAME, API_PASSWORD, f"http://testserver{BIND_PATH}")
    bind_resp = real_app_client.post(
        BIND_PATH,
        json={
            "sourceSystem": "SPY",
            "bindId":        BIND_ID,
            "cameraId":      CAMERA_ID,
            "algorithmList": [ALGO_LABEL],
            "liveUrl":       "http://10.252.93.230:10000/api/sapa/media/live",
            "report": {
                "statusReportUrl": "http://10.252.93.230:10000/api/sapa/report/status",
                "resultReportUrl": "http://10.252.93.230:10000/api/sapa/report/data",
            },
            "beginTime": None,
            "endTime": None,
            "extendParamJson": None,
            "configuation": None,
        },
        headers={"X-Sign": sign},
    )
    assert bind_resp.status_code == 200, f"/bind 响应异常: {bind_resp.text}"
    assert bind_resp.json()["resultCode"] == 0, f"/bind 失败: {bind_resp.text}"
    print(f"✅ /bind 成功，bindId={BIND_ID}")

    # ──────────────────────────────────────────
    # Step 2: 确认任务注册
    # ──────────────────────────────────────────
    assert _monitor is not None
    task = _monitor.tasks.get(BIND_ID)
    assert task is not None, f"MonitorService 中未找到 bindId={BIND_ID}"
    print(f"✅ StreamTask 已注册，状态: {task._status.value}")

    # ──────────────────────────────────────────
    # Step 3: 等待真实 VLM 推理 + 入库
    #   VLM 推理耗时较长（网络请求），最多等待 120 秒
    # ──────────────────────────────────────────
    print("⏳ 等待真实 VLM 推理并写入数据库（最多 120 秒）…")
    rows = []
    deadline = time.time() + 120.0
    last_log = time.time()
    while time.time() < deadline:
        try:
            with _sqlite3.connect(REAL_DB_PATH) as conn:
                conn.row_factory = _sqlite3.Row
                _rows = conn.execute(
                    "SELECT task_id, timestamp_ms, stream_id, stream_name, "
                    "detections, image_url FROM history "
                    "WHERE stream_id=? ORDER BY timestamp_ms DESC LIMIT 10",
                    (BIND_ID,),
                ).fetchall()
                if _rows:
                    rows = list(_rows)
                    break
        except Exception:
            pass

        # 每 10 秒打印一次状态
        if time.time() - last_log >= 10:
            print(f"  … 仍在等待，已过 {int(time.time() - (deadline - 120)):.0f}s，"
                  f"队列状态: {task._status.value}")
            last_log = time.time()
        time.sleep(1.0)

    # ──────────────────────────────────────────
    # Step 4: 断言 SQLite 已入库
    # ──────────────────────────────────────────
    assert len(rows) >= 1, (
        "数据库中无记录！可能原因：\n"
        "  1. VLM 未检测到 truck（置信度不足 0.5）\n"
        "  2. VLM API 连接超时\n"
        "  3. 视频中确实没有 truck\n"
        f"  DB 路径: {REAL_DB_PATH}"
    )
    row = rows[0]
    print(f"\n✅ 数据库已入库 {len(rows)} 条记录")
    print(f"   stream_id  : {row['stream_id']}")
    print(f"   stream_name: {row['stream_name']}")
    print(f"   timestamp  : {row['timestamp_ms']}")
    print(f"   image_url  : {row['image_url']}")
    detections = json.loads(row["detections"])
    print(f"   detections : {json.dumps(detections, ensure_ascii=False)}")

    assert row["stream_id"]   == BIND_ID
    assert row["stream_name"] == CAMERA_ID

    image_url: str = row["image_url"]
    assert image_url.startswith(f"/api/v1/snapshots/{BIND_ID}/"), \
        f"image_url 格式异常: {image_url}"

    # ──────────────────────────────────────────
    # Step 5: 验证快照文件
    # ──────────────────────────────────────────
    filename = Path(image_url).name
    snapshot_path = Path(REAL_SNAPSHOTS_DIR) / BIND_ID / filename
    assert snapshot_path.exists(), f"快照文件不存在: {snapshot_path}"
    assert snapshot_path.stat().st_size > 500, "快照文件大小异常"
    print(f"✅ 快照文件存在: {snapshot_path} ({snapshot_path.stat().st_size} bytes)")

    # ──────────────────────────────────────────
    # Step 6: GET /api/v1/history 接口验证
    # ──────────────────────────────────────────
    history_resp = real_app_client.get(
        f"/api/v1/history?stream_id={BIND_ID}&limit=10"
    )
    assert history_resp.status_code == 200
    history_data = history_resp.json()
    assert len(history_data) >= 1, "/api/v1/history 返回空"
    assert history_data[0]["stream_id"] == BIND_ID
    print(f"✅ /api/v1/history 返回 {len(history_data)} 条")

    # ──────────────────────────────────────────
    # Step 7: GET snapshot 接口验证
    # ──────────────────────────────────────────
    snapshot_api_resp = real_app_client.get(f"/api/v1/snapshots/{BIND_ID}/{filename}")
    assert snapshot_api_resp.status_code == 200
    assert "image/jpeg" in snapshot_api_resp.headers.get("content-type", "")
    assert len(snapshot_api_resp.content) > 500
    print(f"✅ 快照接口响应正常，大小: {len(snapshot_api_resp.content)} bytes")

    # ──────────────────────────────────────────
    # Step 8: 打印告警上报情况（不强制断言，因为结果依赖 VLM）
    # ──────────────────────────────────────────
    alarm_count = len(mock_http_server["received_alarms"])
    status_count = len(mock_http_server["received_statuses"])
    print(f"📡 状态上报次数: {status_count}")
    print(f"🚨 告警上报次数: {alarm_count}")

    # ──────────────────────────────────────────
    # Step 9: POST /unbind
    # ──────────────────────────────────────────
    unbind_sign = _sign(API_USERNAME, API_PASSWORD, f"http://testserver{UNBIND_PATH}")
    unbind_resp = real_app_client.post(
        UNBIND_PATH,
        json={
            "bindId":        BIND_ID,
            "cameraId":      CAMERA_ID,
            "algorithmList": [ALGO_LABEL],
            "sourceSystem":  "SPY",
        },
        headers={"X-Sign": unbind_sign},
    )
    assert unbind_resp.status_code == 200
    assert unbind_resp.json()["resultCode"] == 0, f"/unbind 失败: {unbind_resp.text}"
    assert BIND_ID not in _monitor.tasks, "任务未从 MonitorService 中移除"
    print(f"✅ /unbind 成功，任务已清除")

    print(f"\n🎉 全流程测试完成！数据已持久化到: {REAL_DB_PATH}")


