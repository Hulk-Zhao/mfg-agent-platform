# 多阶段依赖缓存：先装依赖再复制代码，改代码不重装依赖
FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION=1

# 构建期代理（可选）
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY="localhost,127.0.0.1,::1"
ENV http_proxy=${HTTP_PROXY} \
    https_proxy=${HTTPS_PROXY} \
    no_proxy=${NO_PROXY}

# 换清华源 + 装基础工具
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖清单（利用 Docker 缓存层）
COPY pyproject.toml README.md ./

# 安装项目（uv 或 pip）
RUN pip install --no-cache-dir \
    -i https://mirrors.cloud.tencent.com/pypi/simple \
    --timeout 120 --retries 3 \
    --trusted-host mirrors.cloud.tencent.com \
    "."

# 再复制项目代码（改代码不重装依赖）
COPY src ./src
COPY data/init.sql data/seed_v3.sql ./data/

RUN mkdir -p logs data

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8004

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8004/api/v2/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
