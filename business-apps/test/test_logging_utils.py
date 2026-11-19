"""
Unit tests for tools/logging_utils.py — the request-correlation + logging layer.

What is covered:
  - extract_trace_id: header priority order, case-insensitivity, and graceful
    None on every missing link (no request, no headers, broken context).
  - bind_trace_id: honours an inbound trace id; mints a `gen-<uuid>` fallback
    when none is present; actually sets the ContextVar.
  - TraceIdFilter: stamps the current context's trace id onto a LogRecord.
  - setup_logging: installs exactly one handler whose formatter renders the
    trace id, and is idempotent (repeat calls don't stack handlers).
  - tool_call: binds the trace id for the duration of the block, emits start/end
    boundary logs, reports status=error and re-raises on failure.

The context objects are built with the shared `make_ctx` helper from conftest so
these tests exercise the same request shape the real FastMCP Context exposes.
"""

import logging

import pytest

from conftest import make_ctx

from tools import logging_utils
from tools.logging_utils import (
    TraceIdFilter,
    bind_trace_id,
    current_trace_id,
    extract_trace_id,
    setup_logging,
    tool_call,
    trace_id_var,
)


@pytest.fixture(autouse=True)
def _reset_trace_var():
    """Reset the ContextVar around each test so bindings don't leak between them."""
    token = trace_id_var.set("-")
    yield
    trace_id_var.reset(token)


# ── extract_trace_id ────────────────────────────────────────────────────────────

def test_extract_trace_id_prefers_x_trace_id():
    ctx = make_ctx({"x-trace-id": "abc", "x-request-id": "zzz"})
    assert extract_trace_id(ctx) == "abc"


def test_extract_trace_id_is_case_insensitive():
    ctx = make_ctx({"X-Trace-Id": "MixedCase"})
    assert extract_trace_id(ctx) == "MixedCase"


def test_extract_trace_id_falls_back_through_header_priority():
    # No x-trace-id/traceparent — the next header in priority order wins.
    ctx = make_ctx({"x-request-id": "req-1"})
    assert extract_trace_id(ctx) == "req-1"


def test_extract_trace_id_none_when_no_trace_headers():
    ctx = make_ctx({"content-type": "application/json"})
    assert extract_trace_id(ctx) is None


def test_extract_trace_id_none_when_headers_absent():
    # headers=None simulates a request object carrying no headers.
    ctx = make_ctx(headers=None)
    assert extract_trace_id(ctx) is None


def test_extract_trace_id_none_when_context_broken():
    # broken ctx raises AttributeError on request_context access (stdio / unit call).
    ctx = make_ctx(broken=True)
    assert extract_trace_id(ctx) is None


def test_extract_trace_id_none_when_ctx_is_none():
    assert extract_trace_id(None) is None


# ── bind_trace_id ───────────────────────────────────────────────────────────────

def test_bind_trace_id_uses_inbound_header_and_sets_var():
    ctx = make_ctx({"x-trace-id": "inbound-42"})
    returned = bind_trace_id(ctx)
    assert returned == "inbound-42"
    assert current_trace_id() == "inbound-42"


def test_bind_trace_id_generates_fallback_when_absent():
    ctx = make_ctx(headers=None)
    returned = bind_trace_id(ctx)
    assert returned.startswith("gen-")
    assert current_trace_id() == returned


# ── TraceIdFilter ────────────────────────────────────────────────────────────────

def test_trace_id_filter_stamps_record():
    trace_id_var.set("stamp-me")
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hi", args=(), exc_info=None,
    )
    assert TraceIdFilter().filter(record) is True
    assert record.trace_id == "stamp-me"


# ── setup_logging ────────────────────────────────────────────────────────────────

def test_setup_logging_is_idempotent_and_formats_trace(capsys):
    setup_logging()
    first_count = len(logging.getLogger().handlers)
    setup_logging()
    second_count = len(logging.getLogger().handlers)
    # Repeat calls must not stack handlers (which would double-print every line).
    assert first_count == 1
    assert second_count == 1

    trace_id_var.set("fmt-check")
    logging.getLogger("setup_test").info("hello")
    err = capsys.readouterr().err
    assert "[trace=fmt-check]" in err
    assert "hello" in err


# ── tool_call ────────────────────────────────────────────────────────────────────

def test_tool_call_binds_trace_and_logs_boundary(caplog):
    # Don't call setup_logging() here: it replaces the root handlers, which would
    # detach pytest's caplog handler. caplog captures via log propagation instead.
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("tool_call_test")
    ctx = make_ctx({"x-trace-id": "tc-1"})

    with tool_call(ctx, "my_tool", logger) as tid:
        assert tid == "tc-1"
        # Inside the block the var is bound so downstream logs correlate.
        assert current_trace_id() == "tc-1"

    messages = [r.getMessage() for r in caplog.records]
    assert any("my_tool: start trace_id=tc-1" in m for m in messages)
    assert any("my_tool: end status=ok" in m for m in messages)


def test_tool_call_logs_error_status_and_reraises(caplog):
    # See note above: avoid setup_logging() so caplog's handler stays attached.
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("tool_call_err_test")
    ctx = make_ctx({"x-trace-id": "tc-err"})

    def run_failing_tool():
        with tool_call(ctx, "my_tool", logger):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_failing_tool()

    messages = [r.getMessage() for r in caplog.records]
    assert any("my_tool: end status=error" in m for m in messages)
