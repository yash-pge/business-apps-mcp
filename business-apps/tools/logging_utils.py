"""
Logging + request-correlation utilities for the business-apps MCP server (same as ecm-mcp-server).

Everything here exists to make a single MCP request traceable across the modules
it touches (``mcp_search_business_apps`` -> ``business_apps_token`` ->
``business_apps_search``), without
threading a trace id through every function signature.

How it fits together:

  - ``trace_id_var`` is a ``contextvars.ContextVar``. Each inbound request runs in
    its own context (FastMCP/Starlette handle one request per asyncio task), so a
    value set at the start of one request is isolated from any concurrently
    running request. Downstream code never has to accept a ``trace_id`` argument;
    it just reads the current context's value.

  - ``TraceIdFilter`` copies the current ``trace_id_var`` onto every ``LogRecord``
    as ``record.trace_id``, so the formatter can render ``[trace=...]`` on every
    line regardless of which module emitted it.

  - ``setup_logging()`` is the single place that configures the root logger
    (level + formatter + filter). ``server.py`` calls it once at startup; nothing
    else should call ``logging.basicConfig``.

  - ``bind_trace_id`` / ``extract_trace_id`` set the var from an inbound request at
    the tool boundary, generating a ``gen-<uuid>`` fallback so every turn is
    correlatable even when the caller sent no trace header.
"""

import contextvars
import logging
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Optional

# Header names a caller may use to carry a distributed-trace id, in priority
# order. ``x-trace-id`` is what the pge-aigw-apps agents forward, so it comes
# first; the others are common fallbacks emitted by proxies / AWS.
_TRACE_HEADERS = ("x-trace-id", "traceparent", "x-request-id", "x-amzn-trace-id")

# Sentinel shown when no request-scoped trace id has been bound (e.g. startup
# logs, or a non-HTTP transport). Chosen to be visually distinct in log lines.
_NO_TRACE = "-"

# Request-local trace id. Read by TraceIdFilter on every log record; set per
# request by bind_trace_id(). Isolated per asyncio task / context.
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=_NO_TRACE
)

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s [trace=%(trace_id)s] %(message)s"


class TraceIdFilter(logging.Filter):
    """Stamp the current request's trace id onto every log record.

    Attached to the root logger by ``setup_logging``. A filter (rather than a
    custom LogRecordFactory) keeps the behaviour local to this project's handlers
    and is trivially testable.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # getattr guard: a record created before the var is ever set still needs
        # the attribute so the formatter's %(trace_id)s does not blow up.
        if not hasattr(record, "trace_id"):
            record.trace_id = trace_id_var.get()
        return True


def setup_logging() -> None:
    """Configure root logging once, with a trace-aware formatter.

    Level is read from ``LOG_LEVEL`` (default INFO â€” the server runs deployed, so
    DEBUG-by-default would be too noisy). Replaces any handlers already on the
    root logger so repeated calls (e.g. in tests) stay idempotent and never stack
    duplicate handlers or double-print lines.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    handler.addFilter(TraceIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    # Drop existing handlers so we don't double-log if setup runs more than once.
    # list() copy is required: removeHandler mutates root.handlers mid-loop.
    for existing in list(root.handlers):  # NOSONAR - python:S7504 copy needed, removeHandler mutates the list
        root.removeHandler(existing)
    root.addHandler(handler)


def extract_trace_id(ctx: Any) -> Optional[str]:
    """Pull a trace id off an inbound request's headers, or None if absent.

    Walks ``ctx.request_context.request.headers`` (the shape FastMCP exposes for
    the streamable-http transport) and returns the first present trace header.
    Defensive against every missing link in that chain â€” a stdio transport, a
    unit-test Context, or a request with no headers all yield None rather than
    raising, so the caller can fall back to a generated id.
    """
    try:
        request = ctx.request_context.request
    except AttributeError:
        return None
    if request is None:
        return None

    headers = getattr(request, "headers", None)
    if headers is None:
        return None

    for name in _TRACE_HEADERS:
        value = headers.get(name)
        if value:
            return value
    return None


def bind_trace_id(ctx: Any) -> str:
    """Set the request-local trace id from ``ctx`` and return it.

    Prefers an inbound trace header; when none is present, mints a
    ``gen-<uuid>`` so the turn is still correlatable end to end. The returned
    value is also handy for including in a boundary log line.
    """
    trace_id = extract_trace_id(ctx)
    if not trace_id:
        trace_id = f"gen-{uuid.uuid4()}"
    trace_id_var.set(trace_id)
    return trace_id


def current_trace_id() -> str:
    """Return the trace id bound to the current context (or the no-trace marker)."""
    return trace_id_var.get()


@contextmanager
def tool_call(ctx: Any, tool_name: str, logger: logging.Logger):
    """Wrap one tool invocation with trace binding + start/end boundary logs.

    Binds the request-local trace id from ``ctx`` (so every downstream log line
    carries it), logs a start line and â€” on both success and failure â€” an end
    line with elapsed milliseconds and a status. This is the request-boundary
    instrumentation: it records that a call arrived, how long it took, and
    whether it failed, independent of whatever the handler logs internally.

    Usage::

        def my_tool(params, ctx):
            with tool_call(ctx, TOOL_NAME, logger):
                ...  # handler body; any exception is logged then re-raised

    The exception is re-raised unchanged so the handler's own error translation
    (e.g. wrapping as ToolError) still runs.
    """
    trace_id = bind_trace_id(ctx)
    start = time.perf_counter()
    logger.info("%s: start trace_id=%s", tool_name, trace_id)
    try:
        yield trace_id
    except Exception:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("%s: end status=error elapsed_ms=%.1f", tool_name, elapsed_ms)
        raise
    else:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("%s: end status=ok elapsed_ms=%.1f", tool_name, elapsed_ms)
