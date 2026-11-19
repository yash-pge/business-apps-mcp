"""
Tool registry for the Business Apps retriever MCP server.

`server.py` calls:
  - register_tools(mcp)   once at startup, to attach every tool to the FastMCP app
  - registered_tools()    to list (name, description) pairs for /health and boot logs

Each tool module exposes a `register(mcp)` function that decorates its handler with
`@mcp.tool(...)` and appends an (name, description) entry to the shared registry via
`add_tool_descriptor`. Add a new tool by importing its module in `register_tools`.
"""

import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Module-level registry of (name, description) populated as tools register.
_REGISTRY: List[Tuple[str, str]] = []


def add_tool_descriptor(name: str, description: str) -> None:
    """Record a tool's (name, description) for /health and startup logging.

    Idempotent on name so a re-import (e.g. in tests) doesn't duplicate entries.
    """
    for i, (existing, _) in enumerate(_REGISTRY):
        if existing == name:
            logger.debug("Updating existing tool descriptor: %s", name)
            _REGISTRY[i] = (name, description)
            return
    logger.debug("Adding tool descriptor: %s", name)
    _REGISTRY.append((name, description))


def registered_tools() -> List[Tuple[str, str]]:
    """Return a copy of the (name, description) pairs for all registered tools."""
    return list(_REGISTRY)


def register_tools(mcp) -> None:
    """Register every tool with the given FastMCP instance.

    Import each tool module here and call its `register(mcp)`. Keeping the imports
    inside the function (rather than at module top) avoids import-time side effects
    and makes the registration order explicit.
    """
    from . import mcp_search_business_apps

    mcp_search_business_apps.register(mcp)
    logger.info("Registered %d tool(s): %s", len(_REGISTRY), [n for n, _ in _REGISTRY])
