"""
模拟 RTSP 视频流服务
生成蓝色背景 + 时间戳的视频帧，通过 ffmpeg 推送到 RTSP 服务器。

使用前提:
  1. 安装依赖: pip install opencv-python numpy
  2. 安装 ffmpeg 并确保在 PATH 中
  3. 启动一个 RTSP 服务器接收流，例如 mediamtx:
       - 默认监听 rtsp://localhost:8554
       - 本项目自带 mediamtx.yml，可直接运行 mediamtx

运行:
  python mock_rtsp/stream.py

自定义推流地址:
  python mock_rtsp/stream.py --rtsp rtsp://localhost:8554/mock
"""

import argparse
import subprocess
import sys
import time
from datetime import datetime

import cv2
import numpy as np


def parse_args():
    parser = argparse.ArgumentParser(description="模拟 RTSP 视频流 (蓝色背景 + 时间戳)")
    parser.add_argument(
        "--rtsp",
        default="rtsp://localhost:8554/mock",
        help="RTSP 推流地址 (默认: rtsp://localhost:8554/mock)",
    )
    parser.add_argument("--width", type=int, default=1280, help="画面宽度")
    parser.add_argument("--height", type=int, default=720, help="画面高度")
    parser.add_argument("--fps", type=int, default=25, help="帧率")
    return parser.parse_args()


def make_frame(width: int, height: int) -> np.ndarray:
    """生成一帧蓝色背景 + 时间戳的图片。"""
    # 深蓝色背景 (BGR)
    frame = np.full((height, width, 3), fill_value=0, dtype=np.uint8)
    frame[:] = (120, 50, 10)  # BGR: 深蓝

    # 顶部标题
    title = "Mock RTSP Stream"
    cv2.putText(
        frame, title, (40, 60),
        fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=1.5,
        color=(255, 255, 255), thickness=2, lineType=cv2.LINE_AA,
    )

    # 中间时间戳
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    cv2.putText(
        frame, now, (width // 2 - 300, height // 2),
        fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=2.0,
        color=(0, 255, 255), thickness=3, lineType=cv2.LINE_AA,
    )

    # 底部分辨率信息
    info = f"{width}x{height} @ 25fps"
    cv2.putText(
        frame, info, (40, height - 40),
        fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=1.0,
        color=(200, 200, 200), thickness=1, lineType=cv2.LINE_AA,
    )

    return frame


def main():
    args = parse_args()
    width, height, fps = args.width, args.height, args.fps

    # ffmpeg 通过 stdin 接收原始视频帧并推送到 RTSP
    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-loglevel", "warning",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{width}x{height}",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-f", "rtsp",
        args.rtsp,
    ]

    print(f"[mock-rtsp] 推流地址: {args.rtsp}")
    print(f"[mock-rtsp] 分辨率: {width}x{height} @ {fps}fps")
    print("[mock-rtsp] 启动 ffmpeg ... (Ctrl+C 停止)")

    proc = subprocess.Popen(
        ffmpeg_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    frame_interval = 1.0 / fps
    try:
        while True:
            start = time.time()
            frame = make_frame(width, height)
            try:
                proc.stdin.write(frame.tobytes())
            except BrokenPipeError:
                print("[mock-rtsp] ffmpeg 管道已关闭，退出。", file=sys.stderr)
                break

            # 保持帧率
            elapsed = time.time() - start
            sleep_time = frame_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        print("\n[mock-rtsp] 收到中断信号，停止推流...")
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[mock-rtsp] 已退出。")


if __name__ == "__main__":
    main()
