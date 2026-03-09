# LightMonitor

A lightweight, configuration-driven **Video Stream AI Detection Platform**. The system ingests RTSP video streams, extracts frames at configurable intervals, pushes them via gRPC to an AI detection module, and surfaces results through a web dashboard. Alerts are dispatched to external systems when target labels are detected.

## Architecture

```
┌─────────────┐  gRPC   ┌──────────────────┐  HTTP  ┌───────────────┐
│  Monitor    │ ──────► │  Detection       │ ◄───── │  Frontend     │
│  Service    │         │  Service (gRPC)  │        │  (React+Vite) │
│  (RTSP +    │         │  + FastAPI REST  │        └───────────────┘
│   OpenCV)   │         │  + Alarm Push    │
└─────────────┘         └──────────────────┘
        ▲                        ▲
        └────── config.yaml ─────┘
```

| Component | Tech Stack |
|-----------|-----------|
| Backend   | Python 3.12, FastAPI, gRPC, OpenCV, httpx |
| Frontend  | React 18, TypeScript, Vite, React Router |
| Protocol  | gRPC + Protobuf (frame streaming), REST (UI API) |
| Deploy    | Docker / Docker Compose |

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 20+
- Docker & Docker Compose (for containerised deployment)

### Configuration

Edit `config/config.yaml` to define your RTSP streams, AI model endpoint, alarm webhook, and detection labels.

### Run with Docker Compose

```bash
docker compose up --build
```

- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **gRPC Detection**: localhost:50051

### Local Development

**Backend:**

```bash
cd backend
pip install -r requirements.txt
# Generate gRPC stubs (if proto changes)
python -m grpc_tools.protoc -I../proto \
  --python_out=app/grpc_generated \
  --grpc_python_out=app/grpc_generated \
  ../proto/frame_stream.proto
uvicorn app.main:app --reload
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev
```

### Run Tests

```bash
cd backend
pytest tests/ -v
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/api/v1/tasks` | List all stream tasks |
| `GET`  | `/api/v1/tasks/{task_id}` | Task detail with recent results |
| `GET`  | `/api/v1/tasks/{task_id}/results` | Detection results for a task |
| `GET`  | `/health` | Health check |

## gRPC Service

Defined in `proto/frame_stream.proto`:

```protobuf
service FrameStreamService {
  rpc PushFrame(FrameRequest) returns (PushResponse);
}
```

## Project Structure

```
├── config/config.yaml          # Platform configuration
├── proto/frame_stream.proto    # gRPC service definition
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI entry point
│   │   ├── config.py           # YAML config loader
│   │   ├── models.py           # Pydantic response models
│   │   ├── api/v1/tasks.py     # REST API routes
│   │   ├── services/
│   │   │   ├── monitor.py      # RTSP ingestion + gRPC client
│   │   │   ├── detection.py    # gRPC server + AI inference
│   │   │   └── alarm.py        # Webhook alarm push
│   │   └── grpc_generated/     # Protobuf stubs
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api/client.ts       # API client
│   │   ├── pages/              # TaskList, TaskDetail
│   │   └── components/         # TaskCard, ResultLog
│   └── ...
└── docker-compose.yml
```

## License

MIT