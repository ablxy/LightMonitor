import hashlib

from app.api.v1.algo_auth import generate_basic_signature
from app.config import get_config
from app.main import app
from fastapi.testclient import TestClient


def get_md5(raw_str: str) -> str:
    """计算文本的MD5摘要"""
    hl = hashlib.md5()
    hl.update(raw_str.encode(encoding="utf-8"))
    return hl.hexdigest()


def test_bind_algorithm(tmp_path, monkeypatch):
    import app.main as main_module

    runtime_config = get_config().model_copy(deep=True)
    runtime_config.storage.db_path = str(tmp_path / "bind.db")
    runtime_config.storage.snapshots_dir = str(tmp_path / "snapshots")
    runtime_config.logging.console_enabled = False
    runtime_config.logging.file_enabled = False
    monkeypatch.setattr(main_module, "get_config", lambda: runtime_config)
    payload = {
        "sourceSystem": "SPY",
        "bindId": "00000020061601010301000000000099",
        "cameraId": "00000020061601010301000000000033",
        "algorithmList": ["41812000001", "41812000002"],  # 算法编码列表
        "liveUrl": "rtsp://127.0.0.1/mock",
        "report": {},
        # 可选配置
        "configuation": {"threshold": 80},
    }

    # 测试客户端中对应的完整请求 URL（FastAPI 本地 Client 默认的 base URL 是 http://testserver）
    request_path = "/ai-video-analysis/ai/v1/api/algorithm/bind"
    # 获取配置中的账号密码
    config = get_config()
    username = config.api_auth.username
    password = config.api_auth.password

    headers = {"Authorization": generate_basic_signature(username, password)}

    with TestClient(app) as client:
        response = client.post(request_path, json=payload, headers=headers)
        print(response.json())
        assert response.status_code == 200
        data = response.json()
        assert data["resultCode"] == 0
        assert data["resultDesc"] == "SUCCESS"
