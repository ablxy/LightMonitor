"""HTTP Basic 认证工具 (RFC 7617)。

报文头格式:
    Authorization: Basic <base64(username:password)>

例: 用户名 admin, 密码 123456
    base64("admin:123456") = "YWRtaW46MTIzNDU2"
    对应头: Authorization: Basic YWRtaW46MTIzNDU2
"""

import base64
import binascii
import logging
import secrets

from fastapi import HTTPException, Request

from app.config import get_config
from app.errors import AuthenticationFailedError, AuthenticationRequiredError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def encode_basic_token(username: str, password: str) -> str:
    """生成 Basic 认证 Token (不含 'Basic ' 前缀)。"""
    raw = f"{username}:{password}".encode()
    return base64.b64encode(raw).decode("ascii")


def generate_basic_signature(username: str, password: str) -> str:
    """供客户端使用: 返回完整 Authorization 头内容, 例 'Basic YWRtaW46MTIzNDU2'。"""
    return f"Basic {encode_basic_token(username, password)}"


# ---------------------------------------------------------------------------
# FastAPI 依赖
# ---------------------------------------------------------------------------


async def verify_basic_auth(request: Request) -> bool:
    """
    Basic 鉴权拦截器 (用作 Depends):
    校验请求头 Authorization 中的 Basic 凭据是否与 config.yaml 一致。
    """
    config = get_config()
    expected_username = config.api_auth.username
    expected_password = config.api_auth.password

    auth_header = request.headers.get("Authorization") or request.headers.get(
        "authorization"
    )
    client_ip = request.client.host if request.client else "Unknown"

    # 1. 头缺失 -> 401
    if not auth_header:
        logger.warning(
            "Basic 鉴权失败: 缺少 Authorization 请求头",
            extra={"event": "auth.basic.missing", "client_ip": client_ip},
        )
        raise AuthenticationRequiredError("缺少 Authorization 请求头")

    # 2. 校验 scheme
    parts = auth_header.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        logger.warning(
            "Basic 鉴权失败: Authorization scheme 无效",
            extra={"event": "auth.basic.invalid_scheme", "client_ip": client_ip},
        )
        raise AuthenticationRequiredError("Authorization 必须使用 Basic 认证")

    token = parts[1].strip()

    # 3. base64 解码
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        logger.warning(
            "Basic 鉴权失败: base64 解码失败",
            extra={
                "event": "auth.basic.invalid_base64",
                "client_ip": client_ip,
                "error_type": type(exc).__name__,
            },
        )
        raise AuthenticationRequiredError("Authorization 凭据编码无效")

    # 4. 解析 username:password (只允许出现一次冒号)
    if ":" not in decoded:
        logger.warning(
            "Basic 鉴权失败: 凭据格式错误",
            extra={"event": "auth.basic.malformed", "client_ip": client_ip},
        )
        raise AuthenticationRequiredError("Authorization 凭据格式无效")
    username, password = decoded.split(":", 1)

    # 5. 常量时间比较, 防止时序攻击
    user_ok = secrets.compare_digest(username, expected_username)
    pass_ok = secrets.compare_digest(password, expected_password)

    if not (user_ok and pass_ok):
        logger.warning(
            "Basic 鉴权失败: 用户名或密码不匹配",
            extra={"event": "auth.basic.rejected", "client_ip": client_ip},
        )
        raise AuthenticationFailedError()

    return True


# ---------------------------------------------------------------------------
# 旧版 MD5 接口 (仅保留兼容, 路由层已切换为 verify_basic_auth)
# ---------------------------------------------------------------------------

import hashlib


def get_md5(raw_str: str) -> str:
    """[已弃用] 文本 MD5 摘要。"""
    hl = hashlib.md5()
    hl.update(raw_str.encode(encoding="utf-8"))
    return hl.hexdigest()


async def verify_md5_signature(request: Request):
    """[已弃用] 旧版 X-Sign MD5 鉴权, 仅为兼容外部 import 保留。"""
    config = get_config()
    username = config.api_auth.username
    password = config.api_auth.password
    base_url = f"{request.url.scheme}://{request.url.netloc}/ai-video-analysis"
    expected_md5 = get_md5(f"{username}{password}{base_url}")
    client_sign = request.headers.get("X-Sign")
    if not client_sign:
        raise HTTPException(status_code=401, detail="Missing signature header (X-Sign)")
    if client_sign.lower() != expected_md5.lower():
        raise HTTPException(status_code=403, detail="Signature invalid")
    return True


def generate_md5_signature(url) -> str:
    """[已弃用] 旧版 X-Sign 生成工具。"""
    config = get_config()
    return get_md5(f"{config.api_auth.username}{config.api_auth.password}{url}")
