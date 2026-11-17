"""Input-error handling shared by the tool and the MCP layer.

Two pieces:

* :func:`describe_validation_error` turns a pydantic error into a short
  ``field: reason`` summary that never echoes the offending input.
* :class:`InputErrorMiddleware` closes a gap in the framework. fastmcp validates
  a tool call's arguments against the tool signature *before* the tool function
  runs, and re-raises pydantic's ``ValidationError`` unchanged. That reaches the
  client as raw library text (including a docs URL and the input value), with no
  trace id, and without a log line. The middleware catches it and raises the same
  summarized ``ToolError`` the tool itself uses, with the trace id, and logs it.
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from pydantic import ValidationError

from .logging_config import get_logger
from .tracing import bind_trace_id

logger = get_logger(__name__)


def describe_validation_error(exc: ValidationError) -> str:
    """Summarize a pydantic error as ``field: message`` without echoing input values."""
    parts = []
    for error in exc.errors()[:3]:
        field = ".".join(str(p) for p in error.get("loc", ())) or "input"
        message = str(error.get("msg", "invalid value")).removeprefix("Value error, ")
        parts.append(f"{field}: {message}")
    return "; ".join(parts)


class InputErrorMiddleware(Middleware):
    """Turn framework-level argument validation errors into summarized ToolErrors."""

    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except ValidationError as exc:
            tool = getattr(context.message, "name", "tool")
            trace_id = bind_trace_id()
            detail = describe_validation_error(exc)
            logger.warning(
                "%s: invalid input rejected by schema validation: %s",
                tool,
                detail,
                extra={"tool": tool, "status": "rejected"},
            )
            raise ToolError(f"{tool}: invalid input: {detail} (trace_id={trace_id})") from exc
