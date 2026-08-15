FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（ffmpeg 用于 ASR 音频处理）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（利用 Docker 缓存层：只有 pyproject.toml 变化时才会重装）
COPY pyproject.toml .
# 必须先创建最小的 src 包结构，否则 setuptools 无法发现包
RUN mkdir -p src && touch src/__init__.py && \
    pip install --no-cache-dir -e .

# 复制源码（覆盖 dummy __init__.py）
COPY src/ ./src/
COPY tests/ ./tests/

# 暴露 MCP Server 端口
EXPOSE 8000

# 启动命令（默认 SSE 模式，监听 8000 端口）
CMD ["python", "-m", "src.mcp.server"]