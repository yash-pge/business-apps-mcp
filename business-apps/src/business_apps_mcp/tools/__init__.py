"""Tool registry for the Business Apps MCP server.

Same pattern as ``ecm-mcp-server``'s ``tools/__init__.py``. ``server.py`` calls:

  - ``register_tools(mcp)``   once at startup, to attach every tool to the app
  - ``registered_tools()``    to list (name, description) pairs for ``/health``
                              and the startup log

Each tool lives in its own ``mcp_<name>.py`` module that exposes a
``register(mcp)`` function. That function decorates its handler with
``@mcp.tool(...)`` and records an entry in the shared registry through
:func:`add_tool_descriptor`. To add a tool, create the module and import plus
call it in :func:`register_tools`.
"""

from __future__ import annotations

from ..logging_config import get_logger

logger = get_logger(__name__)

# Module-level registry of (name, description), filled as tools register.
_REGISTRY: list[tuple[str, str]] = []


def add_tool_descriptor(name: str, description: str) -> None:
    """Record a tool's (name, description) for ``/health`` and startup logging.

    Idempotent on ``name`` so building the app more than once in a process
    (tests, tooling) does not duplicate entries.
    """
    for i, (existing, _) in enumerate(_REGISTRY):
        if existing == name:
            logger.debug("Updating existing tool descriptor: %s", name)
            _REGISTRY[i] = (name, description)
            return
    logger.debug("Adding tool descriptor: %s", name)
    _REGISTRY.append((name, description))


def registered_tools() -> list[tuple[str, str]]:
    """Return a copy of the (name, description) pairs for all registered tools."""
    return list(_REGISTRY)


def register_tools(mcp, settings) -> None:
    """Register every tool with the given FastMCP instance.

    ``settings`` lets a tool reflect operator configuration in its schema (for
    example the default ``max_results``). Imports sit inside the function (not at
    module top) to avoid import-time side effects and to keep the registration
    order explicit.
    """
    from . import mcp_search_business_apps

    mcp_search_business_apps.register(mcp, settings)
    logger.info("Registered %d tool(s): %s", len(_REGISTRY), [n for n, _ in _REGISTRY])
