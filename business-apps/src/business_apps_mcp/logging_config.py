"""Structured logging setup.

All logs go to stdout. On ECS/Fargate the container's stdout is collected by
the platform's log driver (CloudWatch), so the application never ships logs
itself: its job is to make every line self-describing and one-line-per-event.

Produces JSON logs in production (one JSON object per line, easy to filter in
CloudWatch / an observability stack) and human-readable logs for local
development. Both carry the request's ``trace_id`` on every line, so one
request can be followed end to end. Call :func:`configure_logging` once at
startup.
"""

from __future__ import annotations

import logging
import sys

try:  # python-json-logger >= 3
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # python-json-logger 2.x (what requirements.txt pins today)
    from pythonjsonlogger.jsonlogger import JsonFormatter

from .tracing import TraceIdFilter

_CONFIGURED = False

# uvicorn installs its own handlers (plain text) when started from the CLI, as
# in the Docker image. Route its logs through our handler so the container emits
# a single, consistent format.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class ServiceFieldsFilter(logging.Filter):
    """Add static ``service`` and ``version`` fields to every record."""

    def __init__(self, service: str, version: str) -> None:
        super().__init__()
        self._service = service
        self._version = version

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self._service
        record.version = self._version
        return True


def configure_logging(
    level: str = "INFO",
    fmt: str = "json",
    service: str = "business-apps-mcp",
    version: str = "",
) -> None:
    """Configure the root logger. Idempotent — safe to call more than once."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)

    if fmt == "json":
        formatter: logging.Formatter = JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s "
            "%(trace_id)s %(service)s %(version)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    else:
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | [trace=%(trace_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    # Handler-level filters (not logger-level) so they also apply to records
    # propagated from every child logger.
    handler.addFilter(TraceIdFilter())
    handler.addFilter(ServiceFieldsFilter(service, version))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        # Only reroute loggers that uvicorn gave its own handler. A logger with
        # no handler and propagate=False is how ``--no-access-log`` switches the
        # access log off; forcing it to propagate would turn it back on and
        # duplicate the app's own one-line-per-request entry.
        if uvicorn_logger.handlers:
            uvicorn_logger.handlers.clear()
            uvicorn_logger.propagate = True

    # Quiet noisy third-party loggers.
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger."""
    return logging.getLogger(name)
