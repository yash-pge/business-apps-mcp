"""Tests for centralized logging: JSON/console format, trace id on every line."""

from __future__ import annotations

import json
import logging

import pytest

from business_apps_mcp import logging_config
from business_apps_mcp.tracing import trace_id_var


@pytest.fixture(autouse=True)
def _restore_logging(monkeypatch):
    """configure_logging mutates global logging state; put it back after each test."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    saved_uvicorn = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).propagate)
        for name in logging_config._UVICORN_LOGGERS
    }
    monkeypatch.setattr(logging_config, "_CONFIGURED", False)
    token = trace_id_var.set("-")
    yield
    trace_id_var.reset(token)
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for name, (handlers, propagate) in saved_uvicorn.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = handlers
        lg.propagate = propagate


def _json_lines(capsys) -> list[dict]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_json_line_has_standard_and_trace_fields(capsys) -> None:
    logging_config.configure_logging(level="INFO", fmt="json", version="9.9.9")
    trace_id_var.set("t-json")

    logging.getLogger("business_apps_mcp.demo").info("hello", extra={"tool": "search"})

    (line,) = _json_lines(capsys)
    assert line["message"] == "hello"
    assert line["level"] == "INFO"
    assert line["name"] == "business_apps_mcp.demo"
    assert line["trace_id"] == "t-json"
    assert line["service"] == "business-apps-mcp"
    assert line["version"] == "9.9.9"
    assert line["tool"] == "search"  # extra fields are kept as searchable keys
    assert "timestamp" in line


def test_trace_id_is_dash_outside_a_request(capsys) -> None:
    logging_config.configure_logging(level="INFO", fmt="json")
    logging.getLogger("business_apps_mcp.demo").info("startup")
    (line,) = _json_lines(capsys)
    assert line["trace_id"] == "-"


def test_exception_is_a_single_json_line(capsys) -> None:
    # A multi-line plain-text traceback would become many separate CloudWatch events.
    logging_config.configure_logging(level="INFO", fmt="json")
    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        logging.getLogger("business_apps_mcp.demo").exception("failed")

    lines = _json_lines(capsys)
    assert len(lines) == 1
    assert lines[0]["level"] == "ERROR"
    assert "RuntimeError: kaboom" in lines[0]["exc_info"]


def test_console_format_shows_trace(capsys) -> None:
    logging_config.configure_logging(level="INFO", fmt="console")
    trace_id_var.set("t-console")
    logging.getLogger("business_apps_mcp.demo").info("hello")
    out = capsys.readouterr().out
    assert "[trace=t-console]" in out
    assert "hello" in out


def test_level_is_respected(capsys) -> None:
    logging_config.configure_logging(level="WARNING", fmt="json")
    log = logging.getLogger("business_apps_mcp.demo")
    log.info("hidden")
    log.warning("shown")
    assert [line["message"] for line in _json_lines(capsys)] == ["shown"]


def test_uvicorn_logs_use_our_handler(capsys) -> None:
    # uvicorn's CLI installs plain-text handlers; they must be replaced so the
    # container writes one consistent format.
    uvicorn_logger = logging.getLogger("uvicorn.error")
    uvicorn_logger.addHandler(logging.StreamHandler())
    uvicorn_logger.propagate = False

    logging_config.configure_logging(level="INFO", fmt="json")

    assert uvicorn_logger.handlers == []
    assert uvicorn_logger.propagate is True
    uvicorn_logger.info("Application startup complete.")
    (line,) = _json_lines(capsys)
    assert line["message"] == "Application startup complete."
    assert line["name"] == "uvicorn.error"


def test_uvicorn_access_log_with_its_own_handler_is_rerouted(capsys) -> None:
    # Default uvicorn setup (no flag): its plain-text access handler is replaced.
    access = logging.getLogger("uvicorn.access")
    access.addHandler(logging.StreamHandler())
    access.propagate = False

    logging_config.configure_logging(level="INFO", fmt="json")

    assert access.handlers == [] and access.propagate is True
    access.info('127.0.0.1 - "GET /x HTTP/1.1" 200')
    (line,) = _json_lines(capsys)
    assert line["name"] == "uvicorn.access"


def test_disabled_uvicorn_access_log_stays_disabled(capsys) -> None:
    # `--no-access-log` leaves uvicorn.access with no handler and propagate=False.
    # configure_logging must not undo that, or every request is logged twice.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False

    logging_config.configure_logging(level="INFO", fmt="json")

    assert access.propagate is False
    access.info('127.0.0.1 - "GET /x HTTP/1.1" 200')
    assert capsys.readouterr().out == ""


def test_configure_is_idempotent() -> None:
    logging_config.configure_logging(level="INFO", fmt="json")
    logging_config.configure_logging(level="DEBUG", fmt="console")  # ignored
    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.INFO


def test_noisy_libraries_are_quieted() -> None:
    logging_config.configure_logging(level="DEBUG", fmt="json")
    for name in ("botocore", "boto3", "urllib3"):
        assert logging.getLogger(name).level == logging.WARNING
