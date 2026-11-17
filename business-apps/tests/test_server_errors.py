"""Tests for tool-level error handling and trace correlation through the real app."""

from __future__ import annotations

import logging

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from business_apps_mcp import server
from business_apps_mcp.models import RetrievalResult, SearchResponse
from business_apps_mcp.retriever import RetrievalError
from business_apps_mcp.tools import mcp_search_business_apps as search_tool
from business_apps_mcp.tracing import NO_TRACE, TraceIdFilter, trace_id_var

TOOL = "search_business_apps"
# The tool's log lines come from its own module logger.
TOOL_LOGGER = search_tool.__name__
SECRET_QUESTION = "what is the confidential salary policy"


class FakeRetriever:
    """Stands in for KnowledgeBaseRetriever so no AWS call is made."""

    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    def search(self, query, max_results=None, metadata_filter=None):
        self.calls.append({"query": query, "max_results": max_results})
        if self.error:
            raise self.error
        return self.result


def _ok_response() -> SearchResponse:
    return SearchResponse(
        query="q",
        result_count=1,
        reranked=False,
        results=[RetrievalResult(text="chunk", score=0.9, source_uri="s3://b/k")],
    )


@pytest.fixture(autouse=True)
def _reset_trace_var():
    token = trace_id_var.set(NO_TRACE)
    yield
    trace_id_var.reset(token)


@pytest.fixture
def traced_caplog(caplog):
    """caplog whose records also carry trace_id (the real handler adds this filter)."""
    caplog.handler.addFilter(TraceIdFilter())
    caplog.set_level(logging.DEBUG)
    return caplog


def _tool_lines(caplog) -> list[logging.LogRecord]:
    """Request-time log lines written by the tool.

    Excludes the one-off ``Registered tool: ...`` debug line the tool module writes
    when the app is built, which carries no request trace id.
    """
    return [
        r
        for r in caplog.records
        if r.name == TOOL_LOGGER and r.getMessage().startswith(f"{TOOL}:")
    ]


def _use(monkeypatch, fake: FakeRetriever) -> FakeRetriever:
    monkeypatch.setattr(search_tool, "get_retriever", lambda: fake)
    return fake


# -- in-memory client: tool behavior ---------------------------------------
async def test_success_returns_results(settings, monkeypatch) -> None:
    fake = _use(monkeypatch, FakeRetriever(result=_ok_response()))
    async with Client(server.build_mcp(settings)) as client:
        result = await client.call_tool(TOOL, {"query": "how do I get access?", "max_results": 3})
    assert result.structured_content["result_count"] == 1
    assert fake.calls == [{"query": "how do I get access?", "max_results": 3}]


async def test_expected_failure_becomes_tool_error_with_trace_id(settings, monkeypatch) -> None:
    _use(monkeypatch, FakeRetriever(error=RetrievalError("Knowledge Base retrieve failed (AccessDeniedException)")))
    async with Client(server.build_mcp(settings)) as client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool(TOOL, {"query": "q"})
    message = str(exc.value)
    # The useful, safe message reaches the agent...
    assert "Knowledge Base retrieve failed (AccessDeniedException)" in message
    # ...with a trace id the user can quote.
    assert "(trace_id=gen-" in message


async def test_unexpected_failure_is_masked_but_logged(settings, monkeypatch, traced_caplog) -> None:
    _use(monkeypatch, FakeRetriever(error=RuntimeError("db password=hunter2 at 10.0.0.5")))
    async with Client(server.build_mcp(settings)) as client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool(TOOL, {"query": "q"})

    message = str(exc.value)
    assert "hunter2" not in message and "10.0.0.5" not in message
    assert "search_business_apps failed unexpectedly (trace_id=gen-" in message

    # The full detail is kept for operators.
    errors = [r for r in traced_caplog.records if r.levelno == logging.ERROR]
    assert errors and errors[0].exc_info is not None
    assert "hunter2" in repr(errors[0].exc_info[1])


async def test_error_message_trace_id_matches_log_lines(settings, monkeypatch, traced_caplog) -> None:
    _use(monkeypatch, FakeRetriever(error=RetrievalError("nope")))
    async with Client(server.build_mcp(settings)) as client:
        with pytest.raises(ToolError) as exc:
            await client.call_tool(TOOL, {"query": "q"})

    quoted = str(exc.value).split("trace_id=")[1].rstrip(")")
    tool_lines = _tool_lines(traced_caplog)
    assert tool_lines, "expected tool log lines"
    assert {r.trace_id for r in tool_lines} == {quoted}


async def test_query_text_is_never_logged(settings, monkeypatch, traced_caplog) -> None:
    _use(monkeypatch, FakeRetriever(result=_ok_response()))
    async with Client(server.build_mcp(settings)) as client:
        await client.call_tool(TOOL, {"query": SECRET_QUESTION})

    assert SECRET_QUESTION not in traced_caplog.text
    assert f"query_length={len(SECRET_QUESTION)}" in traced_caplog.text


async def test_boundary_logs_have_status_and_duration(settings, monkeypatch, traced_caplog) -> None:
    _use(monkeypatch, FakeRetriever(result=_ok_response()))
    async with Client(server.build_mcp(settings)) as client:
        await client.call_tool(TOOL, {"query": "q"})
    messages = [r.getMessage() for r in _tool_lines(traced_caplog)]
    assert messages[0] == f"{TOOL}: start"
    assert any(m.startswith(f"{TOOL}: end status=ok elapsed_ms=") for m in messages)


async def test_invalid_input_is_rejected_before_the_retriever(settings, monkeypatch) -> None:
    fake = _use(monkeypatch, FakeRetriever(result=_ok_response()))
    async with Client(server.build_mcp(settings)) as client:
        with pytest.raises(ToolError):
            await client.call_tool(TOOL, {"query": "q", "max_results": 0})
    assert fake.calls == []


# -- full HTTP stack: trace id must reach the tool in stateless mode --------
def _rpc_call(arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": TOOL, "arguments": arguments},
    }


_HEADERS = {"accept": "application/json, text/event-stream", "x-api-key": "secret-key-1"}


def test_http_inbound_trace_id_reaches_tool_logs_and_error(settings, monkeypatch, traced_caplog) -> None:
    _use(monkeypatch, FakeRetriever(error=RetrievalError("nope")))
    with TestClient(server.build_app(settings)) as client:
        resp = client.post("/mcp", json=_rpc_call({"query": "q"}), headers={**_HEADERS, "x-trace-id": "http-trace-1"})

    assert resp.status_code == 200
    assert resp.headers["x-trace-id"] == "http-trace-1"
    assert "trace_id=http-trace-1" in resp.text  # in the error the agent sees
    tool_lines = _tool_lines(traced_caplog)
    assert tool_lines and {r.trace_id for r in tool_lines} == {"http-trace-1"}


def test_http_generated_trace_id_is_consistent_everywhere(settings, monkeypatch, traced_caplog) -> None:
    _use(monkeypatch, FakeRetriever(error=RetrievalError("nope")))
    with TestClient(server.build_app(settings)) as client:
        resp = client.post("/mcp", json=_rpc_call({"query": "q"}), headers=_HEADERS)

    generated = resp.headers["x-trace-id"]
    assert generated.startswith("gen-")
    assert f"trace_id={generated}" in resp.text
    tool_lines = _tool_lines(traced_caplog)
    assert {r.trace_id for r in tool_lines} == {generated}


def test_http_requests_do_not_share_trace_ids(settings, monkeypatch, traced_caplog) -> None:
    # The stale-context problem fastmcp has in stateful mode must not appear here.
    _use(monkeypatch, FakeRetriever(error=RetrievalError("nope")))
    with TestClient(server.build_app(settings)) as client:
        a = client.post("/mcp", json=_rpc_call({"query": "q"}), headers={**_HEADERS, "x-trace-id": "req-A"})
        b = client.post("/mcp", json=_rpc_call({"query": "q"}), headers={**_HEADERS, "x-trace-id": "req-B"})
    assert "trace_id=req-A" in a.text and "trace_id=req-B" not in a.text
    assert "trace_id=req-B" in b.text and "trace_id=req-A" not in b.text


def test_http_auth_rejection_is_traced(settings) -> None:
    with TestClient(server.build_app(settings)) as client:
        resp = client.post("/mcp", json=_rpc_call({"query": "q"}), headers={"x-trace-id": "no-key-1"})
    assert resp.status_code == 401
    assert resp.headers["x-trace-id"] == "no-key-1"
    assert resp.json()["trace_id"] == "no-key-1"
