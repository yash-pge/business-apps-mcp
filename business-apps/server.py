"""
MCP server exposing the Business Apps retriever tools, built on FastMCP.

Same layout and auth model as ecm-mcp-server.

Transport: Streamable HTTP
  MCP endpoint:   http://<host>:<port>/mcp
  Health:         http://<host>:<port>/health
"""

import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server.fastmcp import FastMCP

from tools import register_tools, registered_tools
from tools.logging_utils import setup_logging

# ── Configuration ───────────────────────────────────────────────────────────

load_dotenv()

SERVER_NAME = "business-apps-search"
PORT = int(os.getenv("MCP_PORT", "5000"))

# Bind address. Defaults to loopback so a bare `python server.py` is never
# exposed on every interface; the container opts in via MCP_HOST=0.0.0.0.
BIND_HOST = os.getenv("MCP_HOST", "127.0.0.1")

# Configure root logging once, with a trace-aware formatter. Level is driven by
# LOG_LEVEL (default INFO). Every module below uses logging.getLogger(__name__)
# and inherits this config; nothing else should call logging.basicConfig.
setup_logging()

logger = logging.getLogger(__name__)

# ── MCP app ─────────────────────────────────────────────────────────────────

logger.info("Initializing %s MCP server on %s:%d", SERVER_NAME, BIND_HOST, PORT)
mcp = FastMCP(
    SERVER_NAME,
    host=BIND_HOST,
    port=PORT,
    stateless_http=True,
)
register_tools(mcp)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    tools = registered_tools()
    logger.debug("Health check: %d tool(s) registered", len(tools))
    return JSONResponse({
        "status":           "ok",
        "server":           SERVER_NAME,
        "port":             PORT,
        "tools_registered": len(tools),
        "tools":            [name for name, _ in tools],
        "timestamp":        datetime.now(timezone.utc).isoformat(),
    })


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _tools = registered_tools()

    print("")
    print(f"  [{SERVER_NAME}]  Business Apps Retriever MCP Server")
    print(f"  Port     : {PORT}")
    print(f"  Endpoint : http://{BIND_HOST}:{PORT}/mcp")
    print(f"  Health   : http://{BIND_HOST}:{PORT}/health")
    print("")
    print(f"  Tools ({len(_tools)}):")
    for _name, _desc in _tools:
        print(f"    {_name:<26} {_desc[:60]}")
    print("")

    logger.info("Starting %s on http://%s:%d/mcp (streamable-http)", SERVER_NAME, BIND_HOST, PORT)
    mcp.run(transport="streamable-http")
