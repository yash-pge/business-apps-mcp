"""Request tracing: one trace id per request, stamped on every log line.

This follows the same approach as ``ecm-mcp-server`` (``tools/logging_utils.py``):

* A ``ContextVar`` holds the current request's trace id, so no function has to
  accept a ``trace_id`` argument. Each request runs in its own context, so
  concurrent requests never see each other's id.
* The id is taken from the inbound headers (``x-trace-id`` first, which is what
  the pge-aigw-apps agents forward) or generated as ``gen-<uuid>``.
* :class:`TraceIdFilter` copies the id onto every ``LogRecord`` so the log
  formatter can print it, whichever module wrote the line.
* :func:`tool_call` wraps one tool invocation with start/end boundary logs
  (duration + status) and re-raises failures unchanged.

On top of that, :class:`TraceMiddleware` binds the id at the very edge of the
HTTP stack, so the auth middleware's rejections (401/403) and the one-line
request log carry the same id, and the id is returned to the caller in an
``x-trace-id`` response header.
"""

from __future__ import annotations

import contextvars
import logging
import re
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Header names a caller may use to carry a trace id, in priority order.
# ``x-trace-id`` is what the pge-aigw-apps agents forward; the rest are common
# fallbacks emitted by proxies and AWS.
TRACE_HEADERS: tuple[str, ...] = ("x-trace-id", "traceparent", "x-request-id", "x-amzn-trace-id")

# Header used to hand the trace id back to the caller.
RESPONSE_TRACE_HEADER = "x-trace-id"

# Shown when no request-scoped trace id is bound (startup logs, health checks
# that skip tracing, non-HTTP code paths).
NO_TRACE = "-"

# Inbound ids are untrusted input that ends up in logs and response headers, so
# accept only a conservative character set and a bounded length. This covers
# uuids, W3C ``traceparent`` and AWS ``Root=1-...;Parent=...`` values, and
# rejects anything that could forge extra log lines (newlines, control chars).
_SAFE_TRACE_ID = re.compile(r"^[A-Za-z0-9._:;=,/+\- ]{1,128}$")

# Paths that are polled constantly by load balancers; their request log line is
# emitted at DEBUG so it does not drown real traffic.
QUIET_PATHS = frozenset({"/health", "/healthz", "/livez", "/readyz"})

# Request-local trace id. Read by TraceIdFilter on every log record.
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default=NO_TRACE)

access_logger = logging.getLogger("business_apps_mcp.access")


# -- trace id helpers ------------------------------------------------------
def new_trace_id() -> str:
    """Return a fresh, clearly-generated trace id."""
    return f"gen-{uuid.uuid4()}"


def current_trace_id() -> str:
    """Return the trace id bound to the current context (or ``NO_TRACE``)."""
    return trace_id_var.get()


def _first_trace_header(headers: Mapping[str, str] | None) -> str | None:
    """Return the first valid trace id found in ``headers`` (priority order)."""
    if headers is None:
        return None
    for name in TRACE_HEADERS:
        value = headers.get(name)
        if value:
            value = value.strip()
            if _SAFE_TRACE_ID.match(value):
                return value
    return None


def _request_headers(ctx: Any = None) -> Mapping[str, str] | None:
    """Best-effort access to the current HTTP request's headers.

    Tries fastmcp's per-request accessor first, then ``ctx.request_context``
    (the shape ecm-mcp-server uses). Returns ``None`` outside an HTTP request
    (stdio, in-memory clients, unit tests) instead of raising.
    """
    try:
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers()
        if headers:
            return headers
    except (ImportError, RuntimeError, LookupError):
        pass
    try:
        return ctx.request_context.request.headers
    except AttributeError:
        return None


def extract_trace_id(ctx: Any = None) -> str | None:
    """Return the inbound trace id for the current request, or ``None``."""
    return _first_trace_header(_request_headers(ctx))


def bind_trace_id(ctx: Any = None) -> str:
    """Bind and return the trace id for the current request.

    Order of preference: an inbound trace header, then an id already bound
    earlier in this request (by :class:`TraceMiddleware`), then a new one.
    """
    trace_id = extract_trace_id(ctx)
    if not trace_id:
        existing = current_trace_id()
        trace_id = existing if existing != NO_TRACE else new_trace_id()
    trace_id_var.set(trace_id)
    return trace_id


def current_user() -> str:
    """Return the authenticated user for this request, or ``-`` if unknown."""
    try:
        from fastmcp.server.dependencies import get_http_request

        return getattr(get_http_request().state, "user_identity", NO_TRACE) or NO_TRACE
    except (ImportError, RuntimeError, LookupError):
        return NO_TRACE


# -- logging integration ---------------------------------------------------
class TraceIdFilter(logging.Filter):
    """Stamp the current request's trace id onto every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Do not overwrite an id a caller set explicitly via ``extra=``.
        if not hasattr(record, "trace_id"):
            record.trace_id = trace_id_var.get()
        return True


@contextmanager
def tool_call(tool_name: str, log: logging.Logger, ctx: Any = None) -> Iterator[str]:
    """Wrap one tool invocation with trace binding and boundary logs.

    Logs a start line and, on success *and* failure, an end line with the
    elapsed milliseconds and a status. Exceptions are re-raised unchanged so
    the caller's own error translation (``ToolError``) still runs.
    """
    trace_id = bind_trace_id(ctx)
    user = current_user()
    start = time.perf_counter()
    log.info("%s: start", tool_name, extra={"tool": tool_name, "user": user})
    try:
        yield trace_id
    except Exception:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        log.info(
            "%s: end status=error elapsed_ms=%.1f",
            tool_name,
            elapsed_ms,
            extra={"tool": tool_name, "user": user, "status": "error", "elapsed_ms": elapsed_ms},
        )
        raise
    else:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
        log.info(
            "%s: end status=ok elapsed_ms=%.1f",
            tool_name,
            elapsed_ms,
            extra={"tool": tool_name, "user": user, "status": "ok", "elapsed_ms": elapsed_ms},
        )


# -- HTTP edge -------------------------------------------------------------
class TraceMiddleware:
    """Bind a trace id for every HTTP request and return it to the caller.

    Written as plain ASGI (not ``BaseHTTPMiddleware``) so it never interferes
    with streaming responses. It must be the *outermost* middleware so that
    rejections from the auth middleware are traced too.
    """

    def __init__(self, app: ASGIApp, quiet_paths: frozenset[str] = QUIET_PATHS) -> None:
        self.app = app
        self._quiet_paths = quiet_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = _first_trace_header(Headers(scope=scope))
        trace_id = inbound or new_trace_id()
        if not inbound:
            # Make the generated id visible to code that reads request headers
            # later (the tool wrapper), so the whole request shares one id.
            scope["headers"] = [*scope["headers"], (RESPONSE_TRACE_HEADER.encode(), trace_id.encode())]

        token = trace_id_var.set(trace_id)
        start = time.perf_counter()
        status = {"code": 500}  # reported if the app raises before responding

        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                MutableHeaders(scope=message)[RESPONSE_TRACE_HEADER] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
            user = (scope.get("state") or {}).get("user_identity", NO_TRACE)
            level = logging.DEBUG if scope["path"] in self._quiet_paths else logging.INFO
            access_logger.log(
                level,
                "%s %s -> %s elapsed_ms=%.1f",
                scope["method"],
                scope["path"],
                status["code"],
                elapsed_ms,
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status["code"],
                    "elapsed_ms": elapsed_ms,
                    "user": user,
                },
            )
            trace_id_var.reset(token)
