"""Application error types and FastAPI exception handlers."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.observability import get_request_id

logger = logging.getLogger(__name__)


class AppError(Exception):
    """A safe, machine-readable error that may cross an API boundary."""

    code = "INTERNAL_ERROR"
    status_code = 500
    public_message = "服务内部错误"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.public_message)
        self.message = message or self.public_message
        self.details = details
        self.context = context or {}


class ServiceNotReadyError(AppError):
    code = "SERVICE_NOT_READY"
    status_code = 503
    public_message = "服务尚未准备完成"


class AuthenticationRequiredError(AppError):
    code = "AUTHENTICATION_REQUIRED"
    status_code = 401
    public_message = "缺少或无法识别认证凭据"
    headers: ClassVar[dict[str, str]] = {
        "WWW-Authenticate": 'Basic realm="LightMonitor"'
    }


class AuthenticationFailedError(AppError):
    code = "AUTHENTICATION_FAILED"
    status_code = 403
    public_message = "用户名或密码错误"
    headers: ClassVar[dict[str, str]] = {
        "WWW-Authenticate": 'Basic realm="LightMonitor"'
    }


class TaskNotFoundError(AppError):
    code = "TASK_NOT_FOUND"
    status_code = 404
    public_message = "任务不存在"


class InvalidPathError(AppError):
    code = "INVALID_PATH"
    status_code = 400
    public_message = "文件路径无效"


class SnapshotNotFoundError(AppError):
    code = "SNAPSHOT_NOT_FOUND"
    status_code = 404
    public_message = "抓拍图片不存在"


class StreamConnectionError(AppError):
    code = "STREAM_CONNECTION_FAILED"
    status_code = 502
    public_message = "视频流连接失败"


class ModelInferenceError(AppError):
    code = "MODEL_INFERENCE_FAILED"
    status_code = 502
    public_message = "模型推理失败"


class ModelResponseError(AppError):
    code = "MODEL_RESPONSE_INVALID"
    status_code = 502
    public_message = "模型响应格式无效"


class ExternalServiceError(AppError):
    code = "EXTERNAL_SERVICE_FAILED"
    status_code = 502
    public_message = "外部服务调用失败"


def error_payload(
    code: str,
    message: str,
    *,
    details: Any = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": get_request_id(),
            "details": details,
        }
    }


def install_exception_handlers(app: FastAPI) -> None:
    """Install the public error contract without exposing internal exceptions."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "application request failed",
            extra={
                "event": "api.request.failed",
                "error_code": exc.code,
                "method": request.method,
                "path": request.url.path,
                **exc.context,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
            content=error_payload(exc.code, exc.message, details=exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "reason": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        logger.info(
            "request validation failed",
            extra={
                "event": "api.request.validation_failed",
                "method": request.method,
                "path": request.url.path,
                "field_count": len(details),
            },
        )
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "VALIDATION_ERROR", "请求参数校验失败", details=details
            ),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "请求处理失败"
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=error_payload(f"HTTP_{exc.status_code}", message),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled request error",
            extra={
                "event": "api.request.unhandled_error",
                "method": request.method,
                "path": request.url.path,
                "error_type": type(exc).__name__,
            },
        )
        return JSONResponse(
            status_code=500,
            content=error_payload("INTERNAL_ERROR", "服务内部错误"),
        )
