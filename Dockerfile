# git-asset-api-mcp-base — MCP server container
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖（利用构建缓存）
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir "setuptools>=68" && \
    pip install --no-cache-dir .

# 拷贝源码（可编辑源码 + 生成物目录）
COPY src ./src
COPY config ./config
COPY templates ./templates
COPY examples ./examples

# 数据与生成物目录（挂载卷可覆盖）
RUN mkdir -p /app/data /app/generated

EXPOSE 8000

# 默认以 stdio 启动（供 MCP client 作为子进程拉起）；
# 需要 HTTP 时覆盖 CMD：["git-asset-mcp", "serve", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8000"]
ENTRYPOINT ["git-asset-mcp"]
CMD ["serve"]
