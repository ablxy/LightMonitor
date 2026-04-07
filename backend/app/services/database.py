"""SQLite persistence layer – stores detection history records.

Inspired by the Frigate NVR project's approach to event storage:
- WAL journal mode for concurrent reads/writes
- Simple schema with JSON-serialised detections column
- Async wrapper via aiosqlite
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from app.models import HistoryRecord

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS history (
    task_id      TEXT    PRIMARY KEY,
    timestamp_ms INTEGER NOT NULL,
    stream_id    TEXT    NOT NULL,
    stream_name  TEXT    NOT NULL,
    detections   TEXT    NOT NULL,
    image_url    TEXT    NOT NULL DEFAULT ''
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_history_stream ON history (stream_id);",
    "CREATE INDEX IF NOT EXISTS idx_history_ts     ON history (timestamp_ms DESC);",
]


class DatabaseService:
    """Async SQLite wrapper for detection history persistence."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open connection, set pragmas, and create schema if needed."""
        db_dir = os.path.dirname(self._db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row

        # Frigate-style: WAL mode + NORMAL sync for durability/performance balance
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            await self._conn.execute(idx_sql)
        await self._conn.commit()
        logger.info("SQLite database opened: %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("SQLite database closed")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write_record(self, record: "HistoryRecord") -> None:
        """Insert a HistoryRecord row; silently skips duplicates (OR IGNORE)."""
        assert self._conn is not None, "DatabaseService not started"
        det_json = json.dumps([d.model_dump() for d in record.detections])
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO history
                (task_id, timestamp_ms, stream_id, stream_name, detections, image_url)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.task_id,
                record.timestamp_ms,
                record.stream_id,
                record.stream_name,
                det_json,
                record.image_url,
            ),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def query_records(
        self,
        stream_id: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return history records as plain dicts, ordered newest-first."""
        assert self._conn is not None, "DatabaseService not started"

        conditions: list[str] = []
        params: list = []

        if stream_id is not None:
            conditions.append("stream_id = ?")
            params.append(stream_id)
        if start_ms is not None:
            conditions.append("timestamp_ms >= ?")
            params.append(start_ms)
        if end_ms is not None:
            conditions.append("timestamp_ms <= ?")
            params.append(end_ms)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT task_id, timestamp_ms, stream_id, stream_name, detections, image_url
            FROM history
            {where}
            ORDER BY timestamp_ms DESC
            LIMIT ?
        """
        params.append(limit)

        async with self._conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()

        records = []
        for row in rows:
            records.append(
                {
                    "task_id": row["task_id"],
                    "timestamp_ms": row["timestamp_ms"],
                    "stream_id": row["stream_id"],
                    "stream_name": row["stream_name"],
                    "detections": json.loads(row["detections"]),
                    "image_url": row["image_url"],
                }
            )
        return records
