"""
Unit tests for the `search_business_apps` MCP tool (tools/mcp_search_business_apps.py).

Same approach as ecm-mcp-server's test_search_gdl.py: the downstream
`search_business_apps_vectordb` and `get_access_token` are patched where the tool
module looks them up, so no network or Entra credentials are needed.
"""

import pytest

from mcp.server.fastmcp.exceptions import ToolError

import tools
from tools import mcp_search_business_apps as module
from tools.mcp_search_business_apps import BusinessAppsResult


def _hit(**overrides):
    hit = {
        "content": {"text": "Request access through the portal."},
        "score": 0.9,
        "location": {"type": "S3", "s3Location": {"uri": "s3://internal-bucket/silver/team/access.md"}},
        "metadata": {
            "category": "BusinessApps",
            "subcategory": "HR",
            "documentName": "Access guide",
            "x-amz-bedrock-kb-chunk-id": "chunk-1",
        },
    }
    hit.update(overrides)
    return hit


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("KB_ID", "KB-1")
    monkeypatch.setenv("API_ENDPOINT", "https://api.example.com/retrieve")


@pytest.fixture
def calls(monkeypatch, configured):
    """Patch the token + search functions; returns a dict recording the calls."""
    record = {"results": [_hit()]}
    monkeypatch.setattr(module, "get_access_token", lambda: "the-token")

    def fake_search(**kwargs):
        record["search"] = kwargs
        if "error" in record:
            raise record["error"]
        return record["results"]

    monkeypatch.setattr(module, "search_business_apps_vectordb", fake_search)
    return record


def test_register_records_descriptor(fake_mcp):
    module.register(fake_mcp)
    assert module.TOOL_NAME in fake_mcp.registered
    assert module.TOOL_NAME in [name for name, _ in tools.registered_tools()]


def test_happy_path_passes_token_and_returns_typed_results(search_tool, calls):
    results = search_tool("how do I request access?", max_results=3)

    assert calls["search"]["access_token"] == "the-token"
    assert calls["search"]["kb_id"] == "KB-1"
    assert calls["search"]["number_of_results"] == 3
    assert calls["search"]["rerank"] is False

    (result,) = results
    assert isinstance(result, BusinessAppsResult)
    assert result.text.startswith("Request access")
    assert result.source_name == "access.md"
    assert result.source_url is None  # S3 path is never returned
    assert result.metadata.document_name == "Access guide"
    dumped = result.model_dump_json()
    assert "s3://" not in dumped and "x-amz-bedrock-kb" not in dumped


def test_sharepoint_link_is_returned(search_tool, calls):
    url = "https://pge.sharepoint.com/sites/a/Guide.aspx"
    calls["results"] = [_hit(location={"type": "SHAREPOINT", "sharePointLocation": {"url": url}})]
    (result,) = search_tool()
    assert result.source_url == url
    assert result.source_name == "Guide.aspx"


def test_rerank_flag_requires_model_arn(search_tool, calls, monkeypatch):
    monkeypatch.setenv("RERANK", "true")
    with pytest.raises(ToolError, match="RERANKING_MODEL_ARN"):
        search_tool()

    monkeypatch.setenv("RERANKING_MODEL_ARN", "arn:test:rerank")
    search_tool()
    assert calls["search"]["rerank"] is True


@pytest.mark.parametrize("missing", ["KB_ID", "API_ENDPOINT"])
def test_missing_configuration_is_named(search_tool, calls, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(ToolError, match=missing):
        search_tool()
    assert "search" not in calls  # nothing sent downstream


@pytest.mark.parametrize("bad", ["", "   ", "x" * 2001])
def test_invalid_query_is_rejected(search_tool, calls, bad):
    with pytest.raises(ToolError, match="invalid input"):
        search_tool(bad)
    assert "search" not in calls


@pytest.mark.parametrize("bad", [0, 26])
def test_max_results_bounds(search_tool, calls, bad):
    with pytest.raises(ToolError, match="invalid input"):
        search_tool(max_results=bad)


def test_missing_entra_fields_are_named_in_auth_error(search_tool, calls, monkeypatch):
    def missing():
        raise RuntimeError("Missing required Entra ID fields: ENTRA_SCOPE")

    monkeypatch.setattr(module, "get_access_token", missing)
    with pytest.raises(ToolError, match="failed during authentication: Missing required Entra ID fields: ENTRA_SCOPE"):
        search_tool()
    assert "search" not in calls


def test_entra_rejection_does_not_leak_provider_message(search_tool, calls, monkeypatch):
    def rejected():
        raise ValueError("AADSTS700027 secret-detail")

    monkeypatch.setattr(module, "get_access_token", rejected)
    with pytest.raises(ToolError) as exc:
        search_tool()
    assert "failed during authentication" in str(exc.value)
    assert "secret-detail" not in str(exc.value)


def test_search_failure_is_distinct_from_auth_failure(search_tool, calls):
    calls["error"] = RuntimeError("search failed with HTTP 503.")
    with pytest.raises(ToolError) as exc:
        search_tool()
    assert "search_business_apps failed: search failed with HTTP 503." in str(exc.value)
    assert "authentication" not in str(exc.value)


@pytest.mark.parametrize(
    "raw",
    [
        [{"score": 0.5}],                       # missing content
        [_hit(content={"text": 123})],          # text not a string
        [_hit(score="high")],                   # score not a number
    ],
)
def test_malformed_records_become_a_safe_tool_error(search_tool, calls, raw):
    calls["results"] = raw
    with pytest.raises(ToolError, match="malformed response"):
        search_tool()
