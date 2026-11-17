"""Tests for trace correlation: header parsing, binding, tool wrapper, middleware."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from business_apps_mcp import tracing
from business_apps_mcp.auth import ApiKeyAuthMiddleware
from business_apps_mcp.config import Settings
from business_apps_mcp.tracing import (
    NO_TRACE,
    TraceIdFilter,
    TraceMiddleware,
    _first_trace_header,
    bind_trace_id,
    current_trace_id,
    extract_trace_id,
    tool_call,
    trace_id_var,
)


@pytest.fixture(autouse=True)
def _reset_trace_var():
    """Reset the ContextVar around each test so bindings never leak between them."""
    token = trace_id_var.set(NO_TRACE)
    yield
    trace_id_var.reset(token)


def make_ctx(headers):
    """Build the ctx shape ecm-mcp-server's tests use: ctx.request_context.request.headers."""
    return SimpleNamespace(request_context=SimpleNamespace(request=SimpleNamespace(headers=headers)))


# -- header parsing --------------------------------------------------------
def test_header_priority_prefers_x_trace_id() -> None:
    headers = {"x-request-id": "req-1", "x-trace-id": "trace-1", "traceparent": "tp-1"}
    assert _first_trace_header(headers) == "trace-1"


def test_header_priority_falls_through_in_order() -> None:
    assert _first_trace_header({"traceparent": "tp-1", "x-request-id": "req-1"}) == "tp-1"
    assert _first_trace_header({"x-request-id": "req-1"}) == "req-1"
    assert _first_trace_header({"x-amzn-trace-id": "Root=1-abc;Sampled=1"}) == "Root=1-abc;Sampled=1"


def test_header_lookup_is_case_insensitive_for_starlette_headers() -> None:
    headers = Headers({"X-Trace-Id": "MixedCase"})
    assert _first_trace_header(headers) == "MixedCase"


def test_no_trace_headers_returns_none() -> None:
    assert _first_trace_header({"content-type": "application/json"}) is None
    assert _first_trace_header(None) is None


@pytest.mark.parametrize(
    "bad",
    ["line1\nforged log line", "has\rcr", "x" * 129, "semi;colon<script>", "tab\there", ""],
)
def test_unsafe_inbound_ids_are_ignored(bad: str) -> None:
    # Inbound ids end up in logs and response headers, so reject anything that
    # could forge log lines or is unreasonably large.
    assert _first_trace_header({"x-trace-id": bad}) is None


def test_unsafe_id_falls_through_to_next_valid_header() -> None:
    headers = {"x-trace-id": "bad\nid", "x-request-id": "req-ok"}
    assert _first_trace_header(headers) == "req-ok"


def test_aws_and_w3c_formats_are_accepted() -> None:
    w3c = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    aws = "Root=1-5759e988-bd862e3fe1be46a994272793;Parent=53995c3f42cd8ad8;Sampled=1"
    assert _first_trace_header({"traceparent": w3c}) == w3c
    assert _first_trace_header({"x-amzn-trace-id": aws}) == aws


# -- extract / bind --------------------------------------------------------
def test_extract_from_ctx_headers() -> None:
    assert extract_trace_id(make_ctx({"x-trace-id": "abc"})) == "abc"


def test_extract_returns_none_for_broken_or_missing_ctx() -> None:
    assert extract_trace_id(None) is None
    assert extract_trace_id(SimpleNamespace()) is None  # no request_context
    assert extract_trace_id(make_ctx(None)) is None  # no headers


def test_bind_uses_inbound_header_and_sets_var() -> None:
    assert bind_trace_id(make_ctx({"x-trace-id": "inbound-42"})) == "inbound-42"
    assert current_trace_id() == "inbound-42"


def test_bind_generates_when_nothing_is_available() -> None:
    trace_id = bind_trace_id(None)
    assert trace_id.startswith("gen-")
    assert current_trace_id() == trace_id


def test_bind_reuses_id_bound_earlier_in_the_request() -> None:
    # TraceMiddleware binds an id first; the tool wrapper must keep it.
    trace_id_var.set("from-middleware")
    assert bind_trace_id(None) == "from-middleware"


# -- logging filter --------------------------------------------------------
def test_filter_stamps_current_trace_id() -> None:
    trace_id_var.set("t-123")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    assert TraceIdFilter().filter(record) is True
    assert record.trace_id == "t-123"


def test_filter_does_not_overwrite_explicit_trace_id() -> None:
    trace_id_var.set("ctx-id")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    record.trace_id = "explicit"
    TraceIdFilter().filter(record)
    assert record.trace_id == "explicit"


# -- tool_call wrapper -----------------------------------------------------
def test_tool_call_logs_start_and_ok_end(caplog) -> None:
    log = logging.getLogger("test.tool_call")
    with caplog.at_level(logging.INFO, logger="test.tool_call"):
        with tool_call("my_tool", log, make_ctx({"x-trace-id": "tc-1"})) as trace_id:
            assert trace_id == "tc-1"
    messages = [r.getMessage() for r in caplog.records]
    assert messages[0] == "my_tool: start"
    assert messages[1].startswith("my_tool: end status=ok elapsed_ms=")
    assert caplog.records[1].status == "ok"
    assert caplog.records[1].tool == "my_tool"


def test_tool_call_logs_error_end_and_reraises(caplog) -> None:
    log = logging.getLogger("test.tool_call")
    with caplog.at_level(logging.INFO, logger="test.tool_call"):
        with pytest.raises(ValueError, match="boom"):
            with tool_call("my_tool", log):
                raise ValueError("boom")
    end = caplog.records[-1]
    assert "status=error" in end.getMessage()
    assert end.status == "error"


# -- HTTP middleware -------------------------------------------------------
def _traced_app(*, with_auth: bool = False, settings: Settings | None = None) -> Starlette:
    seen: dict = {}

    async def echo(request: Request) -> JSONResponse:
        seen["header"] = request.headers.get("x-trace-id")
        seen["var"] = current_trace_id()
        return JSONResponse({"ok": True})

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def boom(_request: Request) -> JSONResponse:
        raise RuntimeError("kaboom")

    middleware = [Middleware(TraceMiddleware)]
    if with_auth:
        middleware.append(Middleware(ApiKeyAuthMiddleware, settings=settings))
    app = Starlette(
        routes=[Route("/mcp", echo), Route("/health", health), Route("/boom", boom)],
        middleware=middleware,
    )
    app.state.seen = seen
    return app


def test_middleware_generates_id_and_returns_it() -> None:
    app = _traced_app()
    resp = TestClient(app).get("/mcp")
    trace_id = resp.headers["x-trace-id"]
    assert trace_id.startswith("gen-")
    # The generated id is visible to the app both as a header and in the ContextVar.
    assert app.state.seen["header"] == trace_id
    assert app.state.seen["var"] == trace_id


def test_middleware_echoes_inbound_id() -> None:
    app = _traced_app()
    resp = TestClient(app).get("/mcp", headers={"x-trace-id": "from-agent-7"})
    assert resp.headers["x-trace-id"] == "from-agent-7"
    assert app.state.seen["var"] == "from-agent-7"


def test_middleware_replaces_unsafe_inbound_id() -> None:
    app = _traced_app()
    resp = TestClient(app).get("/mcp", headers={"x-trace-id": "x" * 500})
    assert resp.headers["x-trace-id"].startswith("gen-")


def test_middleware_uses_fallback_header() -> None:
    resp = TestClient(_traced_app()).get("/mcp", headers={"x-request-id": "req-9"})
    assert resp.headers["x-trace-id"] == "req-9"


def test_each_request_gets_its_own_id() -> None:
    client = TestClient(_traced_app())
    first = client.get("/mcp").headers["x-trace-id"]
    second = client.get("/mcp").headers["x-trace-id"]
    assert first != second


def test_trace_id_is_unbound_after_the_request() -> None:
    TestClient(_traced_app()).get("/mcp", headers={"x-trace-id": "scoped"})
    assert current_trace_id() == NO_TRACE


def test_access_log_line_has_status_duration_and_trace(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="business_apps_mcp.access"):
        TestClient(_traced_app()).get("/mcp", headers={"x-trace-id": "log-1"})
    record = caplog.records[-1]
    assert record.status_code == 200
    assert record.path == "/mcp"
    assert record.method == "GET"
    assert record.elapsed_ms >= 0
    assert "GET /mcp -> 200" in record.getMessage()


def test_health_checks_are_logged_at_debug_only(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="business_apps_mcp.access"):
        TestClient(_traced_app()).get("/health")
    assert caplog.records == []  # quiet at INFO
    with caplog.at_level(logging.DEBUG, logger="business_apps_mcp.access"):
        TestClient(_traced_app()).get("/health")
    assert len(caplog.records) == 1


def test_unhandled_error_is_logged_as_500_and_still_traced(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="business_apps_mcp.access"):
        client = TestClient(_traced_app(), raise_server_exceptions=False)
        resp = client.get("/boom", headers={"x-trace-id": "err-1"})
    assert resp.status_code == 500
    assert caplog.records[-1].status_code == 500


def test_auth_rejection_is_traced_in_header_and_body() -> None:
    # TraceMiddleware is outermost, so even a 401 carries the id the caller can report.
    settings = Settings(kb_id="TESTKB", api_keys="k1")
    app = _traced_app(with_auth=True, settings=settings)
    resp = TestClient(app).get("/mcp", headers={"x-trace-id": "rej-5"})
    assert resp.status_code == 401
    assert resp.headers["x-trace-id"] == "rej-5"
    assert resp.json()["trace_id"] == "rej-5"


def test_auth_forbidden_body_carries_trace_id() -> None:
    from business_apps_mcp.tracing import current_trace_id as _cur  # noqa: F401

    class Stub:
        async def verify(self, _auth):
            return {"username": "u@pge.com", "custom:groups": ["Other"]}

    settings = Settings(
        kb_id="TESTKB", api_keys="", user_auth_enabled=True, allowed_groups="Needed"
    )

    async def echo(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[Route("/mcp", echo)],
        middleware=[
            Middleware(TraceMiddleware),
            Middleware(ApiKeyAuthMiddleware, settings=settings, authenticator=Stub()),
        ],
    )
    resp = TestClient(app).get("/mcp", headers={"x-trace-id": "forb-1", "authorization": "Bearer x"})
    assert resp.status_code == 403
    assert resp.json()["trace_id"] == "forb-1"


def test_non_http_scopes_pass_through() -> None:
    # Lifespan/websocket scopes must not be touched or crash the middleware.
    import asyncio

    called = {}

    async def inner(scope, receive, send):
        called["scope"] = scope["type"]

    mw = TraceMiddleware(inner)
    asyncio.run(mw({"type": "lifespan"}, None, None))
    assert called["scope"] == "lifespan"


def test_module_exposes_expected_header_order() -> None:
    assert tracing.TRACE_HEADERS == ("x-trace-id", "traceparent", "x-request-id", "x-amzn-trace-id")
