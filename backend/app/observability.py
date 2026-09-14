"""Central logging configuration and request correlation helpers."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from app.config import LoggingConfig

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)
_SENSITIVE_PATTERN = re.compile(
    r"(?i)(authorization|token|password|secret|api[_-]?key)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_AUTH_VALUE_PATTERN = re.compile(r"(?i)\b(Basic|Bearer)\s+[A-Za-z0-9._~+/=-]+")
_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str) -> contextvars.Token[str]:
    return _request_id.set(value)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id.reset(token)


def _redact(value: str) -> str:
    value = _SENSITIVE_PATTERN.sub(r"\1\2***", value)
    value = _AUTH_VALUE_PATTERN.sub(r"\1 ***", value)
    return value if len(value) <= 2_000 else f"{value[:2_000]}…[truncated]"


def safe_url(value: str) -> str:
    """Remove credentials, query parameters and fragments from a logged URL."""
    try:
        parts = urlsplit(value)
        hostname = parts.hostname or ""
        if parts.port:
            hostname = f"{hostname}:{parts.port}"
        return urlunsplit((parts.scheme, hostname, parts.path, "", ""))
    except ValueError:
        return "<invalid-url>"


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return _redact(rendered)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.name),
            "message": _redact(record.getMessage()),
            "request_id": getattr(record, "request_id", "-"),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            if key in {"request_id", "event"}:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                data[key] = _redact(value) if isinstance(value, str) else value
        if record.exc_info:
            data["exception"] = _redact(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def configure_logging(config: LoggingConfig) -> None:
    """Configure root logging exactly once for console and optional file output."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.level.upper(), logging.INFO))

    for handler in list(root.handlers):
        if getattr(handler, "_lightmonitor_handler", False):
            root.removeHandler(handler)
            handler.close()

    formatter: logging.Formatter
    if config.format.lower() == "json":
        formatter = JsonFormatter()
    else:
        formatter = TextFormatter(
            "%(asctime)s [%(levelname)s] %(name)s "
            "[request_id=%(request_id)s]: %(message)s"
        )

    handlers: list[logging.Handler] = []
    if config.console_enabled:
        handlers.append(logging.StreamHandler(sys.stdout))

    if config.file_enabled:
        file_path = Path(config.resolved_file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            TimedRotatingFileHandler(
                file_path,
                when=config.rotate_when,
                backupCount=config.backup_count,
                encoding="utf-8",
                utc=True,
                delay=True,
            )
        )

    for handler in handlers:
        handler._lightmonitor_handler = True  # type: ignore[attr-defined]
        handler.addFilter(ContextFilter())
        handler.setFormatter(formatter)
        root.addHandler(handler)

    # Let the application handlers own uvicorn output after startup.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
