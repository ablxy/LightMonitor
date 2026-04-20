import hashlib
import logging # 引入 logging
from fastapi import Request, HTTPException
from app.config import get_config

logger = logging.getLogger(__name__) # 初始化 logger

def get_md5(raw_str: str) -> str:
    """计算文本的MD5摘要"""
    hl = hashlib.md5()
    hl.update(raw_str.encode(encoding='utf-8'))
    return hl.hexdigest()

async def verify_md5_signature(request: Request):
    """
    鉴权拦截器:
    用于校验请求方发来的MD5摘要是否匹配。
    """
    config = get_config()
    username = config.api_auth.username
    password = config.api_auth.password
    
    # 动态获取当前请求的协议头 (http/https) 和主机地址 (IP:PORT)
    # 然后固定拼接上 `/ai-video-analysis`
    # 结果例如: http://10.253.88.138:8080/ai-video-analysis
    base_url = f"{request.url.scheme}://{request.url.netloc}/ai-video-analysis"
    
    # 拼接方式: 账号名 + 密码 + 截断后的请求URL
    str_to_sign = f"{username}{password}{base_url}"
    
    expected_md5 = get_md5(str_to_sign)
    
    # 获取对方传过来的 Header
    client_sign = request.headers.get("X-Sign")  
    
    if not client_sign:
        logger.warning(f"鉴权失败: 缺少 X-Sign 请求头. 来源IP: {request.client.host if request.client else 'Unknown'}")
        raise HTTPException(status_code=401, detail="Missing signature header (X-Sign)")
        
    if client_sign.lower() != expected_md5.lower():
        # 添加日志，方便排查字符串拼接是否一致
        logger.error(f"鉴权失败. 预期MD5: {expected_md5}, 客户端发来: {client_sign}")
        logger.error(f"预期MD5的拼接字符串为: '{str_to_sign}'")
        raise HTTPException(status_code=403, detail="Signature invalid")
        
    return True

def generate_md5_signature(url) -> str:
    """生成MD5签名的工具函数，供客户端使用"""
    config = get_config()
    username = config.api_auth.username
    password = config.api_auth.password

    str_to_sign = f"{username}{password}{url}"
    return get_md5(str_to_sign)