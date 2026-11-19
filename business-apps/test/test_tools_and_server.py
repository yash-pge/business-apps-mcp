"""Tests for the tool registry and server.py wiring (same as ecm-mcp-server)."""

import asyncio
import json

import tools


def test_add_tool_descriptor_is_idempotent_and_updates(monkeypatch):
    monkeypatch.setattr(tools, "_REGISTRY", [])

    tools.add_tool_descriptor("a", "first")
    tools.add_tool_descriptor("b", "other")
    tools.add_tool_descriptor("a", "second")

    assert tools.registered_tools() == [("a", "second"), ("b", "other")]


def test_registered_tools_returns_a_copy(monkeypatch):
    monkeypatch.setattr(tools, "_REGISTRY", [("a", "d")])

    tools.registered_tools().append(("x", "y"))

    assert tools.registered_tools() == [("a", "d")]


def test_server_registers_tools_and_health_reports_them():
    import server

    names = {name for name, _ in tools.registered_tools()}
    assert "search_business_apps" in names

    response = asyncio.run(server.health(None))
    body = json.loads(response.body)

    assert body["status"] == "ok"
    assert body["server"] == server.SERVER_NAME
    assert body["tools_registered"] == len(body["tools"])
    assert "search_business_apps" in body["tools"]


def test_server_binds_to_loopback_by_default():
    import server

    # The test environment does not set MCP_HOST, so the safe default applies.
    assert server.BIND_HOST in ("127.0.0.1", __import__("os").getenv("MCP_HOST", "127.0.0.1"))
