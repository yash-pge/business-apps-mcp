# syntax=docker/dockerfile:1

# ---- base -----------------------------------------------------------------
FROM python:3.11-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ---- dependencies (cached layer) -----------------------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---- application ----------------------------------------------------------
# README.md is required by the build backend: pyproject.toml declares it as the
# package readme, and the install fails without it (it is un-ignored in
# .dockerignore for that reason).
COPY README.md pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

# Run as non-root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# The container must listen on all interfaces (the app defaults to loopback), and
# logs at INFO. LOG_LEVEL is the same variable name ecm-mcp-server uses.
ENV BAMCP_HOST=0.0.0.0 \
    BAMCP_PORT=8080 \
    LOG_LEVEL=INFO

EXPOSE 8080

# Container healthcheck hits the unauthenticated /health route.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)" || exit 1

# Start through the app's own entry point (like ecm-mcp-server's `python3 server.py`).
# It binds BAMCP_HOST:BAMCP_PORT, logs the endpoint, health URL and tools at
# startup, and runs uvicorn with its access log off (the app writes its own
# one-line-per-request JSON entry with the trace id and duration).
CMD ["python", "-m", "business_apps_mcp.server"]
