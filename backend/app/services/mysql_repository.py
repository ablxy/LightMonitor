"""MySQL 5.7-compatible repository for persisted analysis alarms.

The repository is intentionally independent from Flask and worker lifecycle so
Saver can use it directly and the Web layer can share the same query contract.
Payloads remain JSON strings for MySQL 5.7 compatibility.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


class MysqlRepository:
    def __init__(self, engine: Any):
        self.engine = engine

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def insert_alarm(self, alarm: dict[str, Any]) -> bool:
        """Insert an alarm idempotently by ``event_id``.

        Returns False when the event already exists. The SQL uses ``INSERT
        IGNORE`` because the schema's unique key provides the idempotency guard
        and this syntax is supported by MySQL 5.7.
        """
        from sqlalchemy import text  # type: ignore[import-not-found]

        statement = text(
            """
            INSERT IGNORE INTO alarm_record (
                event_id, task_id, source_id, algorithm_code, alarm_time,
                record_type, status, detections_json, vlm_result_json,
                snapshot_object_key, video_object_key
            ) VALUES (
                :event_id, :task_id, :source_id, :algorithm_code, :alarm_time,
                :record_type, :status, :detections_json, :vlm_result_json,
                :snapshot_object_key, :video_object_key
            )
            """
        )
        params = {
            "event_id": alarm["event_id"],
            "task_id": alarm["task_id"],
            "source_id": alarm["source_id"],
            "algorithm_code": alarm["algorithm_code"],
            "alarm_time": alarm.get("alarm_time", datetime.utcnow()),
            "record_type": alarm.get("record_type", "alarm"),
            "status": alarm.get("status", "unhandled"),
            "detections_json": self._json(alarm.get("detections", [])),
            "vlm_result_json": self._json(alarm["vlm_result"])
            if alarm.get("vlm_result") is not None
            else None,
            "snapshot_object_key": alarm.get("snapshot_object_key"),
            "video_object_key": alarm.get("video_object_key"),
        }
        with self.engine.begin() as connection:
            result = connection.execute(statement, params)
        return bool(result.rowcount)

    def list_alarms(
        self,
        *,
        source_id: str | None = None,
        algorithm_code: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return recent alarms with optional indexed filters."""
        from sqlalchemy import text  # type: ignore[import-not-found]

        limit = max(1, min(limit, 100))
        clauses = []
        params: dict[str, Any] = {}
        if source_id:
            clauses.append("source_id = :source_id")
            params["source_id"] = source_id
        if algorithm_code:
            clauses.append("algorithm_code = :algorithm_code")
            params["algorithm_code"] = algorithm_code
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params["limit"] = limit
        statement = text(
            f"""
            SELECT event_id, task_id, source_id, algorithm_code, alarm_time,
                   record_type, status, detections_json, vlm_result_json,
                   snapshot_object_key, video_object_key
            FROM alarm_record
            {where}
            ORDER BY alarm_time DESC
            LIMIT :limit
            """
        )
        with self.engine.connect() as connection:
            rows = connection.execute(statement, params).mappings().all()
        return [dict(row) for row in rows]
