# LightMonitor

LightMonitor 是一个轻量级、配置驱动的**视频流 AI 分析平台**。系统支持实时拉取 RTSP 视频流，通过异步队列分发帧数据，调用 AI 模型进行检测，并通过 Web 面板实时展示检测结果。当检测到目标标签时，系统会通过 Webhook 向外部系统发送告警，并支持将检测结果图片持久化存储到兼容 S3 的对象存储（RustFS）中。

## 核心架构

系统采用 Python 3.12 + FastAPI 构建单体应用，内部模块通过 \`asyncio.Queue\` 进行高效的异步通信。

\`\`\`
┌─────────────┐  Queue  ┌──────────────────┐  HTTP/S3  ┌───────────────┐
│  Monitor    │ ──────► │  Detection       │ ◄───────► │  Object Store │
│  Service    │         │  Service         │           │  (RustFS/S3)  │
│  (RTSP Reader)        │  (Inference)     │           └───────────────┘
└─────────────┘         └──────────────────┘
        ▲                        │
        │ API Control            │ Webhook (Result/Status)
        │                        ▼
┌─────────────┐         ┌──────────────────┐
│  REST API   │ ◄─────► │  External System │
│  (FastAPI)  │         │  (Callback)      │
└─────────────┘         └──────────────────┘
        ▲
        │ HTTP
        ▼
┌─────────────┐
│  Frontend   │
│  (React)    │
└─────────────┘
\`\`\`

| 组件 | 技术栈 |
|-----------|-----------|
| Backend   | Python 3.12, FastAPI, OpenCV, asyncio, httpx, boto3 |
| Frontend  | React 18, TypeScript, Vite |
| Protocol  | REST API (UI & Control), Webhook (Reporting) |
| Storage   | S3 Compatible (Images), JSONL (Logs) |
| Deploy    | Docker / Docker Compose |

## 快速开始

### 前置要求

- Python 3.12+
- Node.js 20+
- Docker & Docker Compose (推荐)

### 配置文件

编辑 \`config/config.yaml\` 配置 RTSP 流、AI 模型地址、告警 Webhook 和存储配置。

\`\`\`yaml
streams:
  - bindId: "task-001"
    cameraId: "Camera 1"
    live_url: "rtsp://..."
    enabled: true
    frame_extraction:
      fps: 1

detection:
  model_url: "http://process-detection-service/..."

rustfs:
  endpoint: "localhost:9000"
  bucket: "lightmonitor"
\`\`\`

### 使用 Docker Compose 启动

\`\`\`bash
docker compose up --build -d
\`\`\`

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000

### 本地开发

**Backend:**

\`\`\`bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 启动服务
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
\`\`\`

**Frontend:**

\`\`\`bash
cd frontend
npm install
npm run dev
\`\`\`

## API 接口说明

### 1. 任务管理 (Frontend API)

| 方法 | 路径 | 描述 |
|--------|------|-------------|
| \`GET\`  | \`/api/v1/tasks\` | 获取所有视频流任务列表及状态 |
| \`GET\`  | \`/api/v1/tasks/{task_id}\` | 获取单个任务详情及最近检测结果 |
| \`GET\`  | \`/api/v1/tasks/{task_id}/results\` | 获取指定任务的历史检测结果 |

### 2. 算法绑定 (Open API)

路径前缀: \`/ai-video-analysis/ai/v1/api/algorithm\`

| 方法 | 路径 | 描述 |
|--------|------|-------------|
| \`POST\` | \`/bind\` | **动态绑定**: 下发新的分析任务 (支持 MD5 鉴权) |
| \`POST\` | \`/unbind\` | **动态解绑**: 停止并移除分析任务 |

**鉴权说明**:
请求头需携带 \`X-Sign\`，值为 \`MD5(username + password + full_url)\`。默认配置可在 \`config.yaml\` 中修改。

## 项目结构

\`\`\`
├── config/
│   └── config.yaml         # 全局配置文件
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI 入口
│   │   ├── config.py           # 配置加载与 Pydantic 模型
│   │   ├── models.py           # 数据模型定义
│   │   ├── api/
│   │   │   ├── v1/
│   │   │   │   ├── tasks.py        # 前端交互 API
│   │   │   │   ├── algo_bind.py    # 算法绑定/解绑 API
│   │   │   │   └── algo_auth.py    # API 鉴权中间件
│   │   ├── services/
│   │   │   ├── monitor.py      # RTSP流读取与任务管理
│   │   │   ├── detection.py    # AI 推理与结果处理 (Queue Consumer)
│   │   │   └── alarm.py        # 告警与回调服务
│   ├── tests/                  # Pytest 测试用例
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api/client.ts       # Axios 封装
│   │   ├── pages/              # 页面组件
│   │   └── components/         # UI 组件
│   ├── package.json
│   └── vite.config.ts
└── docker-compose.yml
\`\`\`

## License

MIT