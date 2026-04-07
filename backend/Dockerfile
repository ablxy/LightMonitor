# ── Stage 1: 安装 Python 依赖 ─────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: 后端运行时镜像 ───────────────────────────────────────────────────
FROM python:3.12-slim AS backend

WORKDIR /app

# OpenCV 运行时所需系统库
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

# 从 builder 阶段复制已安装的 Python 包
COPY --from=builder /install /usr/local

# 复制应用源码
COPY app/ ./app/

# 创建 SQLite 数据库目录、快照目录及日志目录
RUN mkdir -p data/snapshots logs

# 数据目录声明为卷，方便外部挂载持久化
VOLUME ["/app/data", "/app/logs"]

ENV LIGHTMONITOR_CONFIG=/config/config.yaml \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ══════════════════════════════════════════════════════════════════════════════
# 前端部分 — 待前端代码就绪后取消注释
# ══════════════════════════════════════════════════════════════════════════════

# ── Stage 3: 构建前端静态资源 ─────────────────────────────────────────────────
# FROM node:20-alpine AS frontend-builder
#
# WORKDIR /frontend
#
# COPY ../frontend/package*.json ./
# RUN npm ci --omit=dev
#
# COPY ../frontend/ .
# RUN npm run build


# ── Stage 4: Nginx 托管前端 ────────────────────────────────────────────────────
# FROM nginx:1.27-alpine AS frontend
#
# # 将前端构建产物复制到 Nginx 默认静态目录
# COPY --from=frontend-builder /frontend/dist /usr/share/nginx/html
#
# # 若需自定义反向代理配置（转发 /api -> backend:8000），取消下行注释
# # COPY nginx.conf /etc/nginx/conf.d/default.conf
#
# EXPOSE 80
#
# CMD ["nginx", "-g", "daemon off;"]
