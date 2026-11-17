"""Business Apps MCP Server — Streamable HTTP entrypoint.

Same shape as ``ecm-mcp-server``'s ``server.py``: this file builds the FastMCP
app, registers the tools from the ``tools`` package, serves ``/health`` and
starts the server. The tools themselves live in ``tools/mcp_*.py``.

Runs over the MCP Streamable HTTP transport using the standalone ``fastmcp``
framework, as required for internal/custom MCP servers that register behind the
Portkey MCP Gateway.

Run locally:
    python -m business_apps_mcp.server
or (installed):
    business-apps-mcp

The API-key / Cognito / trace middleware is passed to the Streamable HTTP ASGI
app, and the result is served with uvicorn.
"""

from __future__ import annotations

from datetime import datetime, timezone

import uvicorn
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .auth import ApiKeyAuthMiddleware
from .config import Settings, get_settings
from .errors import InputErrorMiddleware
from .logging_config import configure_logging, get_logger
from .tools import register_tools, registered_tools
from .tracing import TraceMiddleware

# Fixed name (not __name__) so log lines say "business_apps_mcp.server" even when
# the module is run as ``python -m business_apps_mcp.server`` (where it would be "__main__").
logger = get_logger("business_apps_mcp.server")

SERVER_NAME = "business-apps"
HEALTH_PATH = "/health"


def build_mcp(settings: Settings) -> FastMCP:
    """Construct the FastMCP application, register its tools and add ``/health``."""
    mcp: FastMCP = FastMCP(
        name=SERVER_NAME,
        instructions=(
            "Semantic search over the PG&E Amplify Business Apps knowledge base "
            "(SharePoint docs, wiki pages, and related content). Use "
            "search_business_apps to retrieve the most relevant source chunks "
            "for a natural-language question, then ground your answer in them and "
            "cite the returned source_uri values."
        ),
        # Unexpected exceptions are reported to the client as a generic error
        # (details stay in the logs). Expected failures are raised as ToolError
        # by the tools, whose message is always shown to the caller.
        mask_error_details=True,
        # Argument-schema violations are rejected by the framework before the
        # tool runs; this summarizes them like every other failure.
        middleware=[InputErrorMiddleware()],
    )

    register_tools(mcp, settings)

    # Lightweight health endpoint for load balancers / Gateway health checks.
    # It is public (no auth), so it reports only what an operator needs to see that
    # the server is up and what it serves. Deployment details such as the
    # Knowledge Base id or AWS region are deliberately not exposed here.
    @mcp.custom_route(HEALTH_PATH, methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        tools = registered_tools()
        logger.debug("Health check: %d tool(s) registered", len(tools))
        return JSONResponse(
            {
                "status": "ok",
                "server": SERVER_NAME,
                "port": settings.port,
                "version": __version__,
                "tools_registered": len(tools),
                "tools": [name for name, _ in tools],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    return mcp


def _log_startup(settings: Settings, host: str | None = None) -> None:
    """Log where the server is reachable, its health URL and the registered tools.

    ``host`` is only known when we start uvicorn ourselves (``main``). Under an
    external uvicorn (factory mode) the bind address comes from its command line,
    so only the paths are logged.
    """
    tools = [name for name, _ in registered_tools()]
    if host:
        base = f"http://{host}:{settings.port}"
        endpoint, health = f"{base}{settings.mcp_path}", f"{base}{HEALTH_PATH}"
    else:
        endpoint, health = settings.mcp_path, HEALTH_PATH
    logger.info(
        "Starting %s: endpoint=%s health=%s tools=%s",
        SERVER_NAME,
        endpoint,
        health,
        tools,
        extra={
            "version": __version__,
            "endpoint": endpoint,
            "health": health,
            "port": settings.port,
            "tools": tools,
            "kb_id": settings.kb_id,
            "region": settings.aws_region,
            "api_key_auth": settings.auth_enabled,
            "user_auth": settings.user_auth_enabled,
            "scope_filter": bool(settings.scope_filter),
            "request_timeout": settings.request_timeout,
            "reranking_enabled": settings.reranking_enabled,
        },
    )


def build_app(settings: Settings | None = None):
    """Return the Streamable HTTP ASGI app with auth middleware attached.

    Middleware is passed via ``http_app(middleware=...)`` (the supported path in
    fastmcp); attaching BaseHTTPMiddleware to the raw ASGI app after creation is
    known to break streaming, so we avoid ``add_middleware`` here.
    """
    settings = settings or get_settings()
    mcp = build_mcp(settings)
    middleware = [
        # Outermost: binds the trace id first so auth rejections are traced too.
        Middleware(TraceMiddleware),
        Middleware(ApiKeyAuthMiddleware, settings=settings),
    ]
    # Stateless: every request is self-contained (no server-side MCP session),
    # so a request can never see another call's HTTP context, and several
    # containers can serve the same client without sticky sessions. The tool is
    # a pure lookup, so no session state is lost.
    return mcp.http_app(path=settings.mcp_path, middleware=middleware, stateless_http=True)


def main() -> None:
    """Console-script / module entrypoint."""
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format, version=__version__)

    app = build_app(settings)
    _log_startup(settings, host=settings.host)

    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,  # defer to our structured logging
        access_log=False,
    )


def create_application():
    """Factory for ASGI servers.

    Use with uvicorn's factory mode so importing this module stays side-effect
    free (important for tests and tooling):

        uvicorn --factory business_apps_mcp.server:create_application
    """
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format, version=__version__)
    app = build_app(settings)
    _log_startup(settings)
    return app


if __name__ == "__main__":
    main()
