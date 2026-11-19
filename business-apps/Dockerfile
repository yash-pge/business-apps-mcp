# Business Apps Search MCP Server Dockerfile (same structure as ecm-mcp-server)
#
# Transport:      Streamable HTTP on port 5000
#   MCP endpoint: http://<host>:5000/mcp
#   Health:       http://<host>:5000/health
#
#   docker build -t mcp-business-apps-search .
#   docker run --rm -p 5000:5000 --env-file .env mcp-business-apps-search
#   curl http://localhost:5000/health
#
# Difference from ecm: dependencies install from requirements.txt with pip
# (there is no uv.lock yet). Switch to `uv sync --frozen` once a lock exists.

ARG IMAGE_TYPE=mcp-business-apps-search
ARG OWNER=pge
ARG ENVIRONMENT=dev

# =============================================================================
# Stage 1: Builder — install Python dependencies into an isolated venv
# =============================================================================
FROM python:3.11-slim AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libffi-dev libssl-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# =============================================================================
# Stage 2: Runtime
# =============================================================================
FROM python:3.11-slim AS runtime

RUN apt-get update && \
    apt-get install -y --no-install-recommends wget && \
    apt-get upgrade -y && \
    rm -rf /var/lib/apt/lists/*

ARG ENVIRONMENT
ARG IMAGE_TYPE
ARG OWNER

LABEL Environment=${ENVIRONMENT}
LABEL Image_Type=${IMAGE_TYPE}
LABEL Owner=${OWNER}
LABEL description="Performs search on the Business Apps knowledge base."
LABEL version="1.0.0"

WORKDIR /app

RUN groupadd -r -g 1500 mcp && \
    useradd  -r -u 1500 -g 1500 -s /bin/bash -d /app mcp && \
    chown -R 1500:1500 /app

COPY --from=builder --chown=1500:1500 /opt/venv /opt/venv

# Application code (server + tools).
COPY --chown=1500:1500 server.py ./
COPY --chown=1500:1500 tools ./tools

RUN mkdir -p /app/tmp && chown 1500:1500 /app/tmp

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TMPDIR=/app/tmp \
    MCP_PORT=5000 \
    MCP_HOST=0.0.0.0 \
    LOG_LEVEL=INFO \
    FASTMCP_TRANSPORT_SECURITY__ENABLE_DNS_REBINDING_PROTECTION=false
USER 1500

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD wget -qO- http://localhost:5000/health >/dev/null 2>&1 || exit 1

CMD ["python3", "server.py"]
