"""Tests for the tools/ registry, the /health tool listing and the bind default.

These cover the structure shared with ecm-mcp-server: one ``mcp_<name>.py`` module
per tool exposing ``register(mcp)``, a registry the server reads, and a ``/health``
route that reports what is registered.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastmcp import Client, FastMCP
from starlette.testclient import TestClient

from business_apps_mcp import server, tools
from business_apps_mcp.config import Settings
from business_apps_mcp.tools import add_tool_descriptor, mcp_search_business_apps, register_tools, registered_tools


@pytest.fixture(autouse=True)
def _clean_registry():
    """Give each test an empty registry and restore the original afterwards."""
    saved = list(tools._REGISTRY)
    tools._REGISTRY.clear()
    yield
    tools._REGISTRY[:] = saved


# -- registry --------------------------------------------------------------
def test_register_tools_records_the_search_tool(settings) -> None:
    register_tools(FastMCP("t"), settings)
    names = [name for name, _ in registered_tools()]
    assert names == ["search_business_apps"]
    description = dict(registered_tools())["search_business_apps"]
    assert description == mcp_search_business_apps.TOOL_DESCRIPTION


def test_registering_twice_does_not_duplicate(settings) -> None:
    # Building the app more than once (tests, tooling) must not grow the registry.
    register_tools(FastMCP("a"), settings)
    register_tools(FastMCP("b"), settings)
    assert len(registered_tools()) == 1


def test_add_tool_descriptor_updates_an_existing_name() -> None:
    add_tool_descriptor("x", "first")
    add_tool_descriptor("x", "second")
    assert registered_tools() == [("x", "second")]


def test_registered_tools_returns_a_copy() -> None:
    add_tool_descriptor("x", "d")
    registered_tools().append(("evil", "d"))
    assert registered_tools() == [("x", "d")]


# -- tool is actually exposed over MCP -------------------------------------
async def test_tool_is_listed_with_description_and_schema(settings) -> None:
    async with Client(server.build_mcp(settings)) as client:
        listed = await client.list_tools()
    assert [t.name for t in listed] == ["search_business_apps"]
    tool = listed[0]
    assert tool.description == mcp_search_business_apps.TOOL_DESCRIPTION
    assert tool.inputSchema["required"] == ["query"]
    assert set(tool.inputSchema["properties"]) == {"query", "max_results", "metadata_filter"}


# -- /health ---------------------------------------------------------------
def test_health_reports_required_fields(settings) -> None:
    with TestClient(server.build_app(settings)) as client:
        resp = client.get("/health")  # public: no API key needed
    assert resp.status_code == 200
    body = resp.json()
    # Acceptance criteria: status, server name, port, tool count/names, timestamp.
    assert body["status"] == "ok"
    assert body["server"] == server.SERVER_NAME
    assert body["port"] == settings.port
    assert body["tools_registered"] == 1
    assert body["tools"] == ["search_business_apps"]
    datetime.fromisoformat(body["timestamp"])  # a real ISO-8601 timestamp
    assert "version" in body


def test_health_does_not_leak_deployment_internals(settings) -> None:
    # /health is unauthenticated, so it must not expose backend details.
    with TestClient(server.build_app(settings)) as client:
        body = client.get("/health").json()
    for internal in ("kb_id", "region", "reranking"):
        assert internal not in body
    assert settings.kb_id not in str(body)


# -- bind address ----------------------------------------------------------
def test_default_bind_host_is_loopback() -> None:
    # Never exposed on every interface by default (same choice as ecm-mcp-server).
    assert Settings(kb_id="TESTKB").host == "127.0.0.1"


def test_bind_host_can_be_opened_explicitly() -> None:
    assert Settings(kb_id="TESTKB", host="0.0.0.0").host == "0.0.0.0"
