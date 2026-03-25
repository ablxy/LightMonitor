import hashlib
from fastapi import Request, HTTPException
from app.config import get_config

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
    
    # 路径部分是请求的完整URL： http://IP:PORT/ai-video-analysis
    url_to_sign = str(request.url) 
    
    # 常见的拼接方式是: 账号名 + 密码 + 请求URL (你可以根据需要调整这里的拼接顺序)
    str_to_sign = f"{username}{password}{url_to_sign}"
    
    expected_md5 = get_md5(str_to_sign)
    
    # 假设对方将生成的MD5签名放在了请求头 `Authorization` 或 `X-Sign` 中
    client_sign = request.headers.get("X-Sign")  # 请替换成第三方实际要求的鉴权Header名
    
    if not client_sign:
        raise HTTPException(status_code=401, detail="Missing signature header (X-Sign)")
        
    if client_sign.lower() != expected_md5.lower():
        # 如果验证失败，可以打印日志排查
        # print(f"Auth failed. Expected: {expected_md5}, Client sent: {client_sign}, String used: {str_to_sign}")
        raise HTTPException(status_code=403, detail="Signature invalid")
        
    return True


def generate_md5_signature(url) -> str:
    """生成MD5签名的工具函数，供客户端使用"""
    config = get_config()
    username = config.api_auth.username
    password = config.api_auth.password

    str_to_sign = f"{username}{password}{url}"
    return get_md5(str_to_sign)