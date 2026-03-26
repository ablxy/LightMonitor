import hashlib
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

# 导入应用组件
from app.api.v1.tasks import init_router as init_task_router, router as task_router
from app.api.v1.algo_bind import init_binding_router, router as bind_router
from app.config import AppConfig

# ------------------------------------------------------------------
# 1. 模拟服务 (Mocks)
# ------------------------------------------------------------------

class MockStreamTask:
    """模拟单个流任务的状态对象"""
    def __init__(self, bind_id, name, labels):
        self.stream_id = bind_id
        self.stream_name = name
        self.status = "running"  # 模拟任务正在运行
        self.labels = labels
        self.latest_frame_ts = 1700000000000

class MockMonitorService:
    """模拟监控服务，负责管理任务的增删查改"""
    def __init__(self):
        self.tasks = {}

    async def init_single_stream(self, cfg):
        # 创建一个模拟任务并保存
        task = MockStreamTask(cfg.bindId, cfg.cameraId, cfg.labels)
        self.tasks[cfg.bindId] = task

    async def remove_single_stream(self, bind_id):
        if bind_id in self.tasks:
            del self.tasks[bind_id]
            return True
        return False

class MockDetectionService:
    """模拟检测服务，负责返回识别结果"""
    def get_recent_results(self, task_id, limit=10):
        # 返回空列表或模拟数据
        return []

# ------------------------------------------------------------------
# 2. 测试夹具 (Fixtures)
# ------------------------------------------------------------------

@pytest.fixture
def monitor_service():
    return MockMonitorService()

@pytest.fixture
def detection_service():
    return MockDetectionService()

@pytest.fixture
def client(monitor_service, detection_service):
    """初始化 FastAPI 应用并注入 Mock 服务"""
    app = FastAPI()
    
    # 初始化路由并注入 Mock 服务
    init_task_router(monitor_service, detection_service)
    init_binding_router(monitor_service)
    
    # 注册路由
    app.include_router(task_router)
    app.include_router(bind_router)
    
    return TestClient(app)

@pytest.fixture
def auth_credentials():
    """获取默认的鉴权配置"""
    cfg = AppConfig()
    return cfg.api_auth.username, cfg.api_auth.password

# ------------------------------------------------------------------
# 3. 辅助函数
# ------------------------------------------------------------------

def calculate_md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def generate_signature(username, password, full_url):
    """生成 API 签名: MD5(username + password + full_url)"""
    return calculate_md5(f"{username}{password}{full_url}")

# ------------------------------------------------------------------
# 4. 全流程测试用例
# ------------------------------------------------------------------

def test_full_lifecycle_process(client, auth_credentials):
    """
    测试任务的完整生命周期：
    1. Bind (创建任务)
    2. Verify (验证任务存在)
    3. Unbind (删除任务)
    4. Verify Removal (验证任务已删除)
    """
    username, password = auth_credentials
    bind_id = "test_task_001"
    camera_id = "Test Camera 1"
    
    # =================================================================
    # 步骤 1: 绑定任务 (POST /bind)
    # =================================================================
    bind_endpoint = "/ai-video-analysis/ai/v1/api/algorithm/bind"
    # TestClient 默认使用 http://testserver
    full_bind_url = f"http://testserver{bind_endpoint}"
    
    # 生成签名
    signature = generate_signature(username, password, full_bind_url)
    
    bind_payload = {
        "sourceSystem": "TEST_SYSTEM",
        "bindId": bind_id,
        "cameraId": camera_id,
        "algorithmList": ["person_detect", "fire_detect"],
        "liveUrl": "rtsp://127.0.0.1/stream1",
        "report": {
            "statusReportUrl": "http://callback/status",
            "resultReportUrl": "http://callback/result"
        }
    }
    
    headers = {"X-Sign": signature}
    
    response = client.post(bind_endpoint, json=bind_payload, headers=headers)
    assert response.status_code == 200, f"Bind failed: {response.text}"
    json_resp = response.json()
    assert json_resp["resultCode"] == 0
    assert json_resp["resultDesc"] == "SUCCESS" or "成功" in json_resp["resultDesc"]

    # =================================================================
    # 步骤 2: 验证任务已创建 (GET /tasks/{id})
    # =================================================================
    # 检查单个任务详情
    response = client.get(f"/api/v1/tasks/{bind_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["task"]["stream_id"] == bind_id
    assert data["task"]["stream_name"] == camera_id
    assert "person_detect" in data["task"]["labels"]
    assert data["task"]["status"] == "running"
    
    # 检查任务列表
    response = client.get("/api/v1/tasks")
    assert response.status_code == 200
    tasks = response.json()
    task_ids = [t["stream_id"] for t in tasks]
    assert bind_id in task_ids

    # =================================================================
    # 步骤 3: 解绑任务 (POST /unbind)
    # =================================================================
    unbind_endpoint = "/ai-video-analysis/ai/v1/api/algorithm/unbind"
    full_unbind_url = f"http://testserver{unbind_endpoint}"
    
    signature_unbind = generate_signature(username, password, full_unbind_url)
    
    unbind_payload = {
        "sourceSystem": "TEST_SYSTEM",
        "bindId": bind_id,
        "cameraId": camera_id,
        "algorithmList": [] 
    }
    
    headers_unbind = {"X-Sign": signature_unbind}
    
    response = client.post(unbind_endpoint, json=unbind_payload, headers=headers_unbind)
    assert response.status_code == 200
    assert response.json()["resultCode"] == 0

    # =================================================================
    # 步骤 4: 验证任务已移除
    # =================================================================
    response = client.get(f"/api/v1/tasks/{bind_id}")
    assert response.status_code == 404
    
    response = client.get("/api/v1/tasks")
    tasks = response.json()
    task_ids = [t["stream_id"] for t in tasks]
    assert bind_id not in task_ids
