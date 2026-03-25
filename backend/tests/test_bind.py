from fastapi.testclient import TestClient
from app.main import app
from app.config import get_config
import hashlib
import json

def get_md5(raw_str: str) -> str:
    """计算文本的MD5摘要"""
    hl = hashlib.md5()
    hl.update(raw_str.encode(encoding='utf-8'))
    return hl.hexdigest()

def test_bind_algorithm():
    payload = {
        "sourceSystem": "SPY",
        "bindId": "00000020061601010301000000000099",
        "cameraId": "00000020061601010301000000000033",
        "algorithmList": ["41812000001","41812000002"],  # 算法编码列表
        "liveUrl": "http://1.1.1.1:8080/report/live?platformId=0001",
        "report": {
            "statusReportUrl": "http://1.1.1.1:8080/report/task_status?platformId=0001",
            "resultReportUrl": "http://1.1.1.1:8080/report/task_result?platformId=0001"
        },
        # 可选配置
        "configuation": {
            "threshold": 80
        }
    }

    # 测试客户端中对应的完整请求 URL（FastAPI 本地 Client 默认的 base URL 是 http://testserver）
    request_path = "/ai-video-analysis/ai/v1/api/algorithm/bind"
    full_url = f"http://testserver{request_path}"
    
    # 获取配置中的账号密码
    config = get_config()
    username = config.api_auth.username
    password = config.api_auth.password
    
    # 模拟客户端计算 MD5 签名
    str_to_sign = f"{username}{password}{full_url}"
    client_sign = get_md5(str_to_sign)

    headers = {
        "X-Sign": client_sign  
    }

    with TestClient(app) as client:
        response = client.post(
            request_path, 
            json=payload,
            headers=headers  
        )
        print(response.json())
        assert response.status_code == 200
        data = response.json()
        assert data["resultCode"] == 0
        assert data["resultDesc"] == "SUCCESS"



