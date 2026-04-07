#!/usr/bin/env python3
"""
Manual test script for SQLite database insertion and querying.
Mirrors the approach used by DetectionService when an alarm fires.

Usage:
    export PYTHONPATH=$PYTHONPATH:.
    python3 tests/test_sqlite_manual.py
"""

import asyncio
import os
import sys
import time
import logging
import tempfile

current_dir = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.dirname(current_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.models import BoundingBox, DetectionResult, HistoryRecord
from app.services.database import DatabaseService

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

SEPARATOR = "-" * 50


def _make_record(
    stream_id: str,
    stream_name: str,
    timestamp_ms: int,
    labels: list[str],
    image_url: str = "",
) -> HistoryRecord:
    detections = [
        DetectionResult(
            label=label,
            confidence=0.85,
            bbox=BoundingBox(x_min=10.0, y_min=20.0, x_max=200.0, y_max=300.0),
        )
        for label in labels
    ]
    return HistoryRecord(
        timestamp_ms=timestamp_ms,
        stream_id=stream_id,
        stream_name=stream_name,
        detections=detections,
        image_url=image_url,
    )


async def run_tests(db_path: str) -> None:
    logger.info("Using DB file: %s", db_path)
    db = DatabaseService(db_path)
    await db.start()

    now_ms = int(time.time() * 1000)

    # ------------------------------------------------------------------ #
    # Test 1: 插入单条记录并写回查询
    # ------------------------------------------------------------------ #
    logger.info(SEPARATOR)
    logger.info("Test 1: 插入单条记录并回查")

    r1 = _make_record("stream-001", "Camera A", now_ms, ["person"], "/api/v1/snapshots/stream-001/a.jpg")
    await db.write_record(r1)

    rows = await db.query_records(stream_id="stream-001")
    assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
    assert rows[0]["task_id"] == r1.task_id
    assert rows[0]["stream_id"] == "stream-001"
    assert rows[0]["stream_name"] == "Camera A"
    assert len(rows[0]["detections"]) == 1
    assert rows[0]["detections"][0]["label"] == "person"
    logger.info("PASS – 记录写入并读回成功，task_id=%s", r1.task_id)

    # ------------------------------------------------------------------ #
    # Test 2: 重复插入（OR IGNORE）不报错也不重复
    # ------------------------------------------------------------------ #
    logger.info(SEPARATOR)
    logger.info("Test 2: 重复插入相同 task_id 应被忽略")

    await db.write_record(r1)  # same task_id
    rows = await db.query_records(stream_id="stream-001")
    assert len(rows) == 1, f"Expected 1 row after duplicate insert, got {len(rows)}"
    logger.info("PASS – 重复插入被静默忽略，行数仍为 1")

    # ------------------------------------------------------------------ #
    # Test 3: 多条记录 + 时间范围过滤
    # ------------------------------------------------------------------ #
    logger.info(SEPARATOR)
    logger.info("Test 3: 插入多条记录，验证时间范围过滤")

    r2 = _make_record("stream-001", "Camera A", now_ms - 5000, ["car"])
    r3 = _make_record("stream-001", "Camera A", now_ms - 10000, ["bike"])
    await db.write_record(r2)
    await db.write_record(r3)

    rows_all = await db.query_records(stream_id="stream-001")
    assert len(rows_all) == 3, f"Expected 3 rows, got {len(rows_all)}"
    # 验证默认按时间降序
    assert rows_all[0]["timestamp_ms"] >= rows_all[1]["timestamp_ms"] >= rows_all[2]["timestamp_ms"]
    logger.info("PASS – 3 条记录按时间降序返回")

    rows_filtered = await db.query_records(stream_id="stream-001", start_ms=now_ms - 6000)
    assert len(rows_filtered) == 2, f"Expected 2 rows in time range, got {len(rows_filtered)}"
    logger.info("PASS – 时间范围过滤正确，返回 2 条")

    # ------------------------------------------------------------------ #
    # Test 4: 跨流 ID 过滤
    # ------------------------------------------------------------------ #
    logger.info(SEPARATOR)
    logger.info("Test 4: 不同 stream_id 的记录互不干扰")

    r4 = _make_record("stream-002", "Camera B", now_ms, ["helmet"])
    await db.write_record(r4)

    rows_s1 = await db.query_records(stream_id="stream-001")
    rows_s2 = await db.query_records(stream_id="stream-002")
    rows_no_filter = await db.query_records()

    assert len(rows_s1) == 3
    assert len(rows_s2) == 1
    assert len(rows_no_filter) == 4
    logger.info("PASS – stream-001: %d 条, stream-002: %d 条, 全部: %d 条",
                len(rows_s1), len(rows_s2), len(rows_no_filter))

    # ------------------------------------------------------------------ #
    # Test 5: limit 参数生效
    # ------------------------------------------------------------------ #
    logger.info(SEPARATOR)
    logger.info("Test 5: limit 参数限制返回数量")

    rows_limited = await db.query_records(limit=2)
    assert len(rows_limited) == 2, f"Expected 2 rows with limit=2, got {len(rows_limited)}"
    logger.info("PASS – limit=2 生效，返回 2 条")

    # ------------------------------------------------------------------ #
    # Test 6: 多检测目标序列化 / 反序列化
    # ------------------------------------------------------------------ #
    logger.info(SEPARATOR)
    logger.info("Test 6: 单条记录携带多个 detection")

    r5 = _make_record("stream-003", "Camera C", now_ms, ["person", "car", "bike"])
    await db.write_record(r5)

    rows_s3 = await db.query_records(stream_id="stream-003")
    assert len(rows_s3) == 1
    dets = rows_s3[0]["detections"]
    assert len(dets) == 3
    assert {d["label"] for d in dets} == {"person", "car", "bike"}
    logger.info("PASS – 3 个检测目标正确序列化写入并反序列化读出")

    # ------------------------------------------------------------------ #
    logger.info(SEPARATOR)
    logger.info("所有测试通过 ✓")

    await db.close()


def main() -> None:
    # 使用临时文件，测试完后自动清理
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        asyncio.run(run_tests(db_path))
    finally:
        for ext in ("", "-wal", "-shm"):
            p = db_path + ext
            if os.path.exists(p):
                os.remove(p)
        logger.info("临时数据库文件已清理")


if __name__ == "__main__":
    main()