"""
test_rtsp_bind_and_parse.py

完整流程集成测试：
  1. cv2.VideoWriter 生成合成测试视频（.mp4）
  2. ffmpeg -rtsp_flags listen 把视频暴露为 RTSP 服务
  3. 内嵌 HTTP 服务模拟 liveUrl 端点 → 返回 RTSP URL
  4. 调用真实 /bind 接口，传入 liveUrl 参数
  5. 直接验证 RTSP 流可被 cv2 读取并解析成 BGR 帧
  6. 通过 pytest-asyncio 驱动 StreamTask.start()，验证 MonitorService 消费帧
  7. 调用 /unbind 清理任务

依赖：
  - ffmpeg（brew install ffmpeg）
  - 测试环境: , pytest-asyncio, opencv-ppytestython-headless, numpy, httpx
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np
import pytest

# ============================================================
# Helpers
# ============================================================

def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def _make_sign(username: str, password: str, full_url: str) -> str:
    """生成 X-Sign 请求头签名（与 algo_auth.py 中的逻辑一致）"""
    return _md5(f"{username}{password}{full_url}")


# ============================================================
# Fixtures
# ============================================================

REAL_VIDEO_PATH = "/Users/iecon/Desktop/code/LightMonitor/5086645-uhd_3840_2160_30fps.mp4"


# @pytest.fixture(scope="module")
# def synthetic_video(tmp_path_factory) -> str:
#     """
#     用 cv2.VideoWriter 生成合成测试视频：
#     - 分辨率 320×240，30 fps，共 50 帧
#     - 每帧为随机彩色 BGR 图像（dtype uint8）
#     - 返回临时 .mp4 文件路径
#     """
#     tmp_dir = tmp_path_factory.mktemp("synthetic_video")
#     video_path = str(tmp_dir / "synthetic.mp4")

#     fourcc = cv2.VideoWriter_fourcc(*"mp4v")
#     writer = cv2.VideoWriter(video_path, fourcc, 30.0, (320, 240))
#     assert writer.isOpened(), "cv2.VideoWriter 初始化失败，无法创建合成视频"

#     rng = np.random.default_rng(42)
#     for _ in range(50):
#         frame = rng.integers(0, 256, (240, 320, 3), dtype=np.uint8)
#         writer.write(frame)

#     writer.release()

#     path = Path(video_path)
#     assert path.exists() and path.stat().st_size > 0, "合成视频文件写入失败"
#     return video_path


@pytest.fixture(scope="module")
def real_video() -> str:
    """使用本地已有的 4K MP4 文件作为 ffmpeg 推流源。"""
    path = Path(REAL_VIDEO_PATH)
    assert path.exists(), f"视频文件不存在: {REAL_VIDEO_PATH}"
    assert path.stat().st_size > 0, "视频文件为空"
    print(f"使用测试视频文件: {REAL_VIDEO_PATH} ({path.stat().st_size / 1e6:.2f} MB)")
    return str(path)


@pytest.fixture(scope="module")
def rtsp_server(real_video: str):
    """
    用 ffmpeg 以 -rtsp_flags listen 模式启动 RTSP 服务端。
    ffmpeg 会在 rtsp://127.0.0.1:8554/test 等待首个客户端连入，
    连入后循环推送合成视频帧。

    若系统未安装 ffmpeg，整个 module 所有涉及 RTSP 的测试将被跳过。
    """
    if not shutil.which("ffmpeg"):
        pytest.skip("未找到 ffmpeg，跳过 RTSP 集成测试（brew install ffmpeg）")

    rtsp_url = "rtsp://127.0.0.1:8554/test"
    proc = subprocess.Popen(
        [
            "ffmpeg",
            "-re",                         # 按实时速率读取输入
            "-stream_loop", "-1",          # 无限循环
            "-i", real_video,              # 输入：合成视频文件
            "-vcodec", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-g", "10",                    # 每 10 帧一个关键帧
            "-rtsp_transport", "tcp",
            "-f", "rtsp",
            "-rtsp_flags", "listen",       # ← ffmpeg 作为 RTSP 服务器
            rtsp_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2.0)  # 等 ffmpeg 完成初始化

    if proc.poll() is not None:
        print("ffmpeg 进程启动失败，输出:")
        pytest.skip("ffmpeg RTSP 服务器启动失败")

    yield rtsp_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def live_url_server(rtsp_server: str) -> str:
    """
    模拟第三方 liveUrl 接口：
      GET /live  →  {"resultCode": 0, "url": "rtsp://127.0.0.1:8554/test"}

    MonitorService.get_video_streaming() 会 GET 此地址并解析 url 字段。
    """

    class _LiveUrlHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"resultCode": 0, "url": rtsp_server}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # 静默，不输出 HTTP 访问日志

    port = 18765
    srv = HTTPServer(("127.0.0.1", port), _LiveUrlHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}/live"

    srv.shutdown()


@pytest.fixture(scope="module")
def app_client(live_url_server):
    """
    启动真实 FastAPI app（包括完整 lifespan），
    patch 掉所有外发 HTTP 状态上报，避免网络依赖。
    """
    with patch("app.services.monitor.StreamTask.upload_status", new=AsyncMock()):
        from app.main import app
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


@pytest.fixture(scope="module")
def credentials():
    from app.config import get_config
    cfg = get_config()
    return cfg.api_auth.username, cfg.api_auth.password


# ============================================================
# Test 1 — 单元：合成视频帧解析
# ============================================================

# def test_synthetic_video_frames_are_parseable(synthetic_video: str):
#     """
#     脱离 API，直接用 cv2 读取合成视频：
#     - 确认帧数 == 50
#     - 确认每帧形状 (240, 320, 3)、dtype uint8
#     - 确认每帧可被 JPEG 编码（即 imencode 不失败）
#     """
#     cap = cv2.VideoCapture(synthetic_video)
#     assert cap.isOpened(), f"cv2 无法打开合成视频：{synthetic_video}"

#     frame_count = 0
#     while True:
#         ret, frame = cap.read()
#         if not ret:
#             break

#         assert frame is not None, "帧不应为 None"
#         assert frame.shape == (240, 320, 3), f"帧形状错误: {frame.shape}"
#         assert frame.dtype == np.uint8, f"帧 dtype 错误: {frame.dtype}"

#         ok, buf = cv2.imencode(".jpg", frame)
#         assert ok, f"第 {frame_count} 帧 JPEG 编码失败"
#         jpeg_bytes = buf.tobytes()
#         assert len(jpeg_bytes) > 200, "JPEG 数据过小，疑似空帧"

#         frame_count += 1

#     cap.release()
#     assert frame_count == 50, f"期望 50 帧，实际读取 {frame_count} 帧"


# ============================================================
# Test 2 — 集成：cv2 直接读 RTSP 流
# ============================================================

def test_rtsp_stream_readable_by_cv2(rtsp_server: str):
    """
    cv2.VideoCapture 直接连接 ffmpeg RTSP 服务端，
    读取至少 5 帧并验证：
    - 帧为 3 通道 BGR，dtype uint8
    - JPEG 编码后字节数 > 500（非空帧）
    """
    cap = cv2.VideoCapture(rtsp_server)
    assert cap.isOpened(), f"cv2 无法连接 RTSP 流: {rtsp_server}"

    frames_ok = 0
    for _ in range(5):
        ret, frame = cap.read()
        if not ret:
            break
        assert frame is not None
        assert frame.ndim == 3 and frame.shape[2] == 3, f"帧形状异常: {frame.shape}"
        assert frame.dtype == np.uint8

        ok, buf = cv2.imencode(".jpg", frame)
        assert ok, "JPEG 编码失败"
        assert len(buf.tobytes()) > 500, "JPEG 字节数过小"

        frames_ok += 1

    cap.release()
    assert frames_ok >= 3, f"RTSP 流未能连续读取帧，仅读到 {frames_ok} 帧"


# ============================================================
# Test 3 — 完整流程：bind → liveUrl → RTSP → 帧消费 → unbind
# ============================================================

def test_bind_with_live_url_rtsp_full_flow(
    app_client, live_url_server: str, rtsp_server: str, credentials
):
    """
    完整端到端流程：

    1. POST /bind → liveUrl 指向 mock HTTP 服务
       响应 resultCode == 0

    2. MonitorService.init_single_stream 在 /bind 中被调用，
       创建 StreamTask（状态 INIT）但不自动 start。
       ← 注意：此为现有代码行为，完整自动化需在 init_single_stream 末尾
         补加 `asyncio.create_task(task.start())` 或 `await task.start()`

    3. 从 app.main._monitor.tasks 取出 StreamTask，
       在新的事件循环中调用 task.start()：
         start() → get_video_streaming() → GET live_url_server
         → 拿到 rtsp_url → cv2.VideoCapture(rtsp_url) 读帧
         → latest_frame_ts 被更新

    4. 断言 latest_frame_ts 不为 None

    5. POST /unbind，断言任务从 tasks 中移除
    """
    from app.main import _monitor

    username, password = credentials
    bind_id = "full_flow_rtsp_test_001"
    camera_id = "TestCamera_001"
    bind_path = "/ai-video-analysis/ai/v1/api/algorithm/bind"
    unbind_path = "/ai-video-analysis/ai/v1/api/algorithm/unbind"

    # ------------------------------------------------------------------
    # Step 1: POST /bind
    # ------------------------------------------------------------------
    bind_sign = _make_sign(username, password, f"http://testserver{bind_path}")
    bind_payload = {
        "sourceSystem": "TEST",
        "bindId": bind_id,
        "cameraId": camera_id,
        "algorithmList": ["person_detect"],
        "liveUrl": live_url_server,   # → mock HTTP → 返回 rtsp_server URL
        "report": {
            "statusReportUrl": "http://127.0.0.1:18765/status",
            "resultReportUrl": "http://127.0.0.1:18765/result",
        },
        "configuation": {"threshold": 60},
    }

    resp = app_client.post(bind_path, json=bind_payload, headers={"X-Sign": bind_sign})
    assert resp.status_code == 200, f"/bind 接口异常: {resp.text}"
    bind_data = resp.json()
    assert bind_data["resultCode"] == 0, f"bind 返回错误码: {bind_data}"

    # ------------------------------------------------------------------
    # Step 2: 确认 StreamTask 已注册到 MonitorService
    # ------------------------------------------------------------------
    assert _monitor is not None, "app._monitor 未初始化"
    task = _monitor.tasks.get(bind_id)
    assert task is not None, f"MonitorService.tasks 中未找到 bindId={bind_id}"
    assert task.stream_id == bind_id
    assert task.stream_name == camera_id

    # ------------------------------------------------------------------
    # Step 3: 在独立事件循环中驱动 task.start() → 连接 RTSP → 消费帧
    # (init_single_stream 不自动 start，测试手动触发以覆盖完整链路)
    # ------------------------------------------------------------------
    async def _drive_task(timeout: float = 10.0):
        """启动 StreamTask，等待至少一帧被读取，然后停止。"""
        await task.start()
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if task.latest_frame_ts is not None:
                break
            await asyncio.sleep(0.2)
        await task.stop()

    asyncio.run(_drive_task(timeout=10.0))

    # ------------------------------------------------------------------
    # Step 4: 断言帧已被 MonitorService 成功读取
    # ------------------------------------------------------------------
    assert task.latest_frame_ts is not None, (
        "StreamTask.latest_frame_ts 仍为 None — "
        "cv2 未能从 RTSP 流读取任何帧。\n"
        f"  RTSP URL : {rtsp_server}\n"
        f"  liveUrl  : {live_url_server}"
    )
    assert task.latest_frame_ts > 0, f"latest_frame_ts 应 > 0，实际: {task.latest_frame_ts}"

    # ------------------------------------------------------------------
    # Step 5: POST /unbind，验证任务被彻底移除
    # ------------------------------------------------------------------
    unbind_sign = _make_sign(username, password, f"http://testserver{unbind_path}")
    unbind_resp = app_client.post(
        unbind_path,
        json={"bindId": bind_id, "cameraId": camera_id},
        headers={"X-Sign": unbind_sign},
    )
    assert unbind_resp.status_code == 200, f"/unbind 接口异常: {unbind_resp.text}"
    unbind_data = unbind_resp.json()
    assert unbind_data["resultCode"] == 0, f"unbind 返回错误码: {unbind_data}"
    assert bind_id not in _monitor.tasks, "任务未从 MonitorService.tasks 中移除"