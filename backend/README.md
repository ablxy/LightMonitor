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

### 0. 健康检查

| 方法 | 路径 | 描述 |
|--------|------|-------------|
| `GET`  | `/health` | 服务存活检查, 返回 `{"status": "ok"}` |

---

### 1. 任务管理 (Frontend API)

> 面向前端面板的 REST 接口。`task_id` 即为算法绑定时的 `bindId`。
> 路径前缀: `/api/v1`

| 方法 | 路径 | 描述 |
|--------|------|-------------|
| `GET`  | `/tasks` | 获取所有视频流任务列表及状态 |
| `GET`  | `/tasks/{task_id}` | 获取单个任务详情及最近检测结果 |
| `GET`  | `/tasks/{task_id}/results` | 获取指定任务的历史检测结果 (含 `limit` 查询参数, 默认 20, 范围 1-100) |
| `GET`  | `/history` | 从 SQLite 查询历史记录 (支持按 `stream_id` / `start_ms` / `end_ms` / `limit` 过滤) |
| `GET`  | `/snapshots/{stream_id}/{filename}` | 获取本地存储的抓拍图片 (JPEG) |

#### 1.1 `GET /api/v1/tasks`

**响应** (`List[TaskStatus]`):

| 字段 | 类型 | 说明 |
|------|------|------|
| `stream_id` | string | 即 `bindId`, 任务唯一标识 |
| `stream_name` | string | 即 `cameraId`, 摄像头展示名 |
| `status` | string | 任务状态: `running` / `error` / `offline` / `starting` / `stop` |
| `labels` | string[] | 该任务订阅的算法标签列表 |
| `latest_frame_ts` | int \| null | 最近一帧的时间戳 (毫秒) |

#### 1.2 `GET /api/v1/tasks/{task_id}`

**响应** (`TaskDetail`):

| 字段 | 类型 | 说明 |
|------|------|------|
| `task` | TaskStatus | 同上 |
| `recent_results` | FrameResult[] | 最近若干帧的检测结果 |

**错误码**: `404 Task not found` | `503 Service not ready`

#### 1.3 `GET /api/v1/tasks/{task_id}/results`

**Query 参数**: `limit` (int, 1-100, 默认 20)

**响应**: `FrameResult[]`, 字段说明:

| 字段 | 类型 | 说明 |
|------|------|------|
| `stream_id` | string | `bindId` |
| `stream_name` | string | `cameraId` |
| `timestamp_ms` | int | 帧时间戳 (毫秒) |
| `detections` | DetectionResult[] | 检测结果列表 |
| `alarmed` | bool | 是否触发了告警上报 |
| `image_base64` | string \| null | 抓拍图 Base64 编码 (可选) |

#### 1.4 `GET /api/v1/history`

**Query 参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `stream_id` | string | 否 | 按 `bindId` 过滤 |
| `start_ms` | int | 否 | 起始时间戳 (毫秒, 含) |
| `end_ms` | int | 否 | 截止时间戳 (毫秒, 含) |
| `limit` | int | 否 | 最大返回条数 (1-1000, 默认 100) |

**响应**: `HistoryRecord[]` 数组, 字段: `task_id`, `timestamp_ms`, `stream_id`, `stream_name`, `detections`, `image_url`。

#### 1.5 `GET /api/v1/snapshots/{stream_id}/{filename}`

返回 `image/jpeg` 文件。防止路径穿越 (`..` / `/` 会被拒绝, 返回 `400`)。

---

### 2. 算法绑定 (Open API)

> 面向外部算法平台的动态任务下发/解绑接口。**带 MD5 鉴权中间件**。
> 路径前缀: `/ai-video-analysis/ai/v1/api/algorithm`

| 方法 | 路径 | 描述 |
|--------|------|-------------|
| `POST` | `/bind` | **动态绑定**: 下发新的分析任务 |
| `POST` | `/unbind` | **动态解绑**: 停止并移除分析任务 |

#### 2.1 鉴权说明 (X-Sign MD5)

**所有 `/ai-video-analysis/...` 路径下的请求均需在 Header 中携带 `X-Sign`**:

```
X-Sign: <md5(username + password + base_url)>
```

其中:
- `username` / `password`: 来自 `config.yaml` 的 `api_auth` 段 (默认 `maasadmin` / `Maas@dj0086`)
- `base_url`: 由服务端动态拼接, 格式为 `{scheme}://{host}/ai-video-analysis`
  例: `http://10.253.88.138:8080/ai-video-analysis`

**错误码**:
- `401 Missing signature header (X-Sign)` — 未携带签名
- `403 Signature invalid` — 签名不匹配

#### 2.2 `POST /ai-video-analysis/ai/v1/api/algorithm/bind`

下发一个新的算法分析任务。**异步执行** (通过 `BackgroundTasks` 后台初始化流任务), 接口立即返回。

**请求体** (`BindRequest`):

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sourceSystem` | string | ✅ | 来源系统编码, 如 `SPY` / `HYSP` |
| `bindId` | string (≤48) | ✅ | 任务唯一标识, 整个系统的主键 |
| `cameraId` | string (≤48) | ✅ | 摄像头 ID (仅作展示, 不参与区分任务) |
| `algorithmList` | string[] | ✅ | 订阅的算法编码列表 (即 `labels`) |
| `configuation` | TaskConfig | 否 | 阈值/抑制时间/状态上报周期等 |
| `liveUrl` | string (≤256) | ✅ | 查询实时视频流的 API 地址 |
| `report` | ReportConfig | ✅ | 状态/结果上报地址 (见下) |
| `beginTime` | string | 否 | 任务生效起始时间 |
| `endTime` | string | 否 | 任务生效截止时间 |
| `extendParamJson` | string \| object | 否 | 扩展参数 |

`report` (ReportConfig) 字段:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `status_report_url` | string | 否 | 任务状态变更上报地址 |
| `result_report_url` | string | 否 | 检测结果上报地址 |

`configuation` (TaskConfig) 字段:

| 字段 | 类型 | 说明 |
|------|------|------|
| `threshold` | int | 检测阈值 |
| `holddownTime` | int | 告警抑制时间 (秒) |
| `stateReportTime` | int | 状态上报周期 (秒) |

**服务侧处理逻辑** (重要):

1. **`liveUrl` 自动重写**: 服务端会用正则 `(\d+\.\d+\.)\d+` 把 IP 段第三位之后的最后一段替换为 `245` (例如 `http://10.0.0.12:8080/...` → `http://10.0.0.245:8080/...`), 用于将请求转发到指定的入口虚拟机。
2. **异步初始化**: 接口立即返回成功, 真实的 RTSP 连接 / 帧抓取 / 推理在后台任务中启动。
3. **重复 `bindId` 行为**: 若 `bindId` 已存在, 则**不会**创建新任务, 而是调用 `update_config()` 覆盖配置; 若仅 `live_url` 变化会触发重启。
4. **流标识唯一性**: 同一 `liveUrl` 可以用不同 `bindId` 创建多个独立任务 (每个任务独立占用一个 RTSP 连接)。`cameraId` 不参与隔离。
5. **回调时机**: 任务状态变化时会向 `status_report_url` 推送 (启动/停止/错误), 每帧有检测结果时向 `result_report_url` 推送 (重试 3 次, 指数退避)。

**响应** (`BindResponse`):

```json
{ "resultCode": 0, "resultDesc": "SUCCESS" }
```

**错误码**: `503 Monitor service not initialized`

#### 2.3 `POST /ai-video-analysis/ai/v1/api/algorithm/unbind`

停止并移除指定的分析任务。

**请求体** (`UnbindRequest`):

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `sourceSystem` | string | ✅ | 来源系统编码 |
| `bindId` | string | ✅ | 要解绑的任务 ID (匹配主键) |
| `cameraId` | string | ✅ | 摄像头 ID (仅记录, 不参与匹配) |
| `algorithmList` | string[] | ✅ | 关联算法列表 (仅记录) |

**响应** (`UnbindResponse`):

成功:
```json
{ "resultCode": 0, "resultDesc": "解绑1条数据" }
```

未找到:
```json
{ "resultCode": 1, "resultDesc": "bindId xxx 不存在" }
```

**错误码**: `503 Monitor service not initialized`

---

### 3. Webhook 上报协议 (Outbound)

> 系统主动调用外部接口, 推送**任务状态**和**检测结果**。上报地址取自 `/bind` 请求中的 `report` 字段。

#### 3.1 状态上报 (Status Report)

**触发时机**: 任务启动 (`STARTING` → `RUNNING`)、停止、错误时各调用一次。

**请求方式**: `POST` (JSON), 上报到 `report.status_report_url`。

**请求体**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `algorithmType` | string | `algorithmList[0]` (首个订阅算法) |
| `bindId` | string | 任务 ID |
| `cameraId` | string | 摄像头 ID |
| `status` | int | `0=INIT` / `1=STARTING` / `2=RUNNING` / `3=STOP` / `4=ERROR` |

#### 3.2 结果上报 (Result Report)

**触发时机**: 任意帧产生检测结果时 (每帧最多 1 次, 重试 3 次, 指数退避)。

**请求方式**: `POST` (JSON), 上报到 `report.result_report_url`。

**请求体**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `captureTime` | string | ✅ | 抓拍时间, 格式 `yyyy-MM-dd HH:mm:ss` |
| `captureBase64` | string | 否 | 全景图 Base64 |
| `detectBase64` | string | 否 | 识别小图 Base64 |
| `desc` | string | 否 | 告警描述 |
| `attributes` | object | 否 | 结构化属性 (允许任意 KV 扩展) |

`attributes` 内部按算法类型分组, 每组结构示例:

```json
{
  "results": [
    { "confidence": 0.91, "x": 100, "y": 200, "width": 50, "height": 80 }
  ],
  "snap": {
    "data": {
      "attributes": {
        "result": {
          "positions": [
            [x_min, y_min, x_max, y_max, "0.9100", "label_name"]
          ]
        }
      }
    }
  }
}
```

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
