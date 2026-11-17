"""Acceptance tests: error handling, observability, validation, output, health.

Each section maps to one block of the user story's acceptance criteria, so a
reviewer can check every criterion against a named test.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest
from botocore import exceptions as boto_exc
from botocore.parsers import ResponseParserError
from fastmcp import Client
from fastmcp.exceptions import ToolError

from business_apps_mcp import retriever as retriever_module
from business_apps_mcp import server
from business_apps_mcp.config import Settings
from business_apps_mcp.filters import (
    MAX_CONDITIONS,
    MAX_DEPTH,
    VALID_OPERATORS,
    FilterError,
    combine_filters,
    validate_filter,
)
from business_apps_mcp.retriever import (
    KnowledgeBaseRetriever,
    RetrievalAuthError,
    RetrievalCallError,
    RetrievalError,
    RetrievalInputError,
    RetrievalResultError,
)
from business_apps_mcp.tools import mcp_search_business_apps as search_tool
from business_apps_mcp.tracing import NO_TRACE, TraceIdFilter, trace_id_var

TOOL = "search_business_apps"
SECRET = "arn:aws:iam::123456789012:role/internal-secret"
REQUEST_ID = "aws-req-7f3c9e"


@pytest.fixture(autouse=True)
def _reset_trace_var():
    token = trace_id_var.set(NO_TRACE)
    yield
    trace_id_var.reset(token)


@pytest.fixture
def traced_caplog(caplog):
    caplog.handler.addFilter(TraceIdFilter())
    caplog.set_level(logging.DEBUG)
    return caplog


def _retriever(settings, client) -> KnowledgeBaseRetriever:
    r = KnowledgeBaseRetriever(settings=settings)
    r._client = client
    return r


def _client_raising(exc: BaseException) -> MagicMock:
    client = MagicMock()
    client.retrieve.side_effect = exc
    return client


def _client_returning(value) -> MagicMock:
    client = MagicMock()
    client.retrieve.return_value = value
    return client


def _client_error(code: str, status: int, message: str = SECRET) -> boto_exc.ClientError:
    return boto_exc.ClientError(
        {
            "Error": {"Code": code, "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status, "RequestId": REQUEST_ID},
        },
        "Retrieve",
    )


def _hit(**overrides) -> dict:
    hit = {
        "content": {"text": "Request access through the portal."},
        "score": 0.9,
        "location": {"type": "S3", "s3Location": {"uri": "s3://internal-bucket/silver/sp/team/access.md"}},
        "metadata": {
            "category": "BusinessApps",
            "subcategory": "HR",
            "documentName": "Access guide",
            "x-amz-bedrock-kb-source-uri": "s3://internal-bucket/silver/sp/team/access.md",
            "x-amz-bedrock-kb-chunk-id": "chunk-1",
            "x-amz-bedrock-kb-data-source-id": "DS123",
        },
    }
    hit.update(overrides)
    return hit


async def _call_tool(settings, monkeypatch, retriever, arguments: dict):
    monkeypatch.setattr(search_tool, "get_retriever", lambda: retriever)
    async with Client(server.build_mcp(settings)) as client:
        return await client.call_tool(TOOL, arguments)


# ==========================================================================
# Error handling: each downstream failure mode is handled separately
# ==========================================================================
@pytest.mark.parametrize(
    ("exc", "expected_type", "kind", "fragment"),
    [
        (boto_exc.ReadTimeoutError(endpoint_url="https://bedrock"), RetrievalCallError, "timeout", "timed out after"),
        (boto_exc.ConnectTimeoutError(endpoint_url="https://bedrock"), RetrievalCallError, "timeout", "timed out after"),
        (boto_exc.EndpointConnectionError(endpoint_url="https://bedrock"), RetrievalCallError, "connection", "could not reach"),
        (boto_exc.ConnectionClosedError(endpoint_url="https://bedrock"), RetrievalCallError, "connection", "could not reach"),
        (ResponseParserError("<html>not json</html>"), RetrievalResultError, "malformed", "malformed response"),
        (boto_exc.NoCredentialsError(), RetrievalAuthError, "auth", "credentials"),
    ],
)
def test_transport_failures_are_classified(settings, exc, expected_type, kind, fragment) -> None:
    with pytest.raises(expected_type) as caught:
        _retriever(settings, _client_raising(exc)).search("q")
    assert caught.value.kind == kind
    assert fragment in str(caught.value)


@pytest.mark.parametrize(
    ("code", "status", "expected_type", "kind"),
    [
        ("AccessDeniedException", 403, RetrievalAuthError, "auth"),
        ("UnrecognizedClientException", 403, RetrievalAuthError, "auth"),
        ("ExpiredTokenException", 400, RetrievalAuthError, "auth"),
        ("ThrottlingException", 429, RetrievalCallError, "throttled"),
        ("ResourceNotFoundException", 404, RetrievalCallError, "not_found"),
        ("ValidationException", 400, RetrievalCallError, "bad_request"),
        ("InternalServerException", 500, RetrievalCallError, "server_error"),
        ("ServiceUnavailableException", 503, RetrievalCallError, "server_error"),
        ("ConflictException", 409, RetrievalCallError, "error"),
    ],
)
def test_http_errors_are_classified_by_status(settings, code, status, expected_type, kind) -> None:
    with pytest.raises(expected_type) as caught:
        _retriever(settings, _client_raising(_client_error(code, status))).search("q")
    assert type(caught.value) is expected_type
    assert caught.value.kind == kind


def test_credential_failure_while_creating_the_client_is_an_auth_error(settings, monkeypatch) -> None:
    def broken_client(*_a, **_k):
        raise boto_exc.ProfileNotFound(profile="missing-profile")

    monkeypatch.setattr(retriever_module.boto3, "client", broken_client)
    with pytest.raises(RetrievalAuthError) as caught:
        KnowledgeBaseRetriever(settings=settings).search("q")
    assert "missing-profile" not in str(caught.value)


@pytest.mark.parametrize(
    "raw",
    [
        ["not", "an", "object"],
        {"retrievalResults": "not-a-list"},
        {"retrievalResults": [{"score": 0.5}]},  # missing content
        {"retrievalResults": [_hit(content={"text": 123})]},  # text not a string
        {"retrievalResults": [_hit(score="high")]},  # score not a number
        {"retrievalResults": [_hit(metadata={"documentName": ["a", "list"]})]},
    ],
)
def test_malformed_upstream_records_fail_validation(settings, raw) -> None:
    with pytest.raises(RetrievalResultError) as caught:
        _retriever(settings, _client_returning(raw)).search("q")
    assert str(caught.value) == "search returned a malformed response"


def test_outward_messages_do_not_contain_backend_internals(settings, traced_caplog) -> None:
    with pytest.raises(RetrievalError) as caught:
        _retriever(settings, _client_raising(_client_error("AccessDeniedException", 403))).search("q")
    message = str(caught.value)
    assert SECRET not in message
    assert REQUEST_ID not in message
    assert "Traceback" not in message

    # ...but the full detail is kept in the internal log.
    record = next(r for r in traced_caplog.records if r.getMessage() == "Bedrock retrieve failed")
    assert record.aws_message == SECRET
    assert record.aws_request_id == REQUEST_ID
    assert record.error_code == "AccessDeniedException"


def test_timeout_is_configurable_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("API_REQUEST_TIMEOUT", "12")
    assert Settings(kb_id="TESTKB").request_timeout == 12

    monkeypatch.delenv("API_REQUEST_TIMEOUT")
    monkeypatch.setenv("BAMCP_REQUEST_TIMEOUT", "45")
    assert Settings(kb_id="TESTKB").request_timeout == 45

    monkeypatch.delenv("BAMCP_REQUEST_TIMEOUT")
    assert Settings(kb_id="TESTKB").request_timeout == 30  # default


@pytest.mark.parametrize("bad", ["0", "-5", "301", "soon"])
def test_invalid_timeout_is_rejected_at_startup(monkeypatch, bad) -> None:
    monkeypatch.setenv("API_REQUEST_TIMEOUT", bad)
    with pytest.raises(ValueError):
        Settings(kb_id="TESTKB")


def test_timeout_is_applied_to_the_aws_client(monkeypatch) -> None:
    captured = {}

    def fake_client(service, config):
        captured["service"], captured["config"] = service, config
        return MagicMock()

    monkeypatch.setattr(retriever_module.boto3, "client", fake_client)
    KnowledgeBaseRetriever(settings=Settings(kb_id="TESTKB", request_timeout=12)).client
    assert captured["service"] == "bedrock-agent-runtime"
    assert captured["config"].read_timeout == 12
    assert captured["config"].connect_timeout == 5

    KnowledgeBaseRetriever(settings=Settings(kb_id="TESTKB", request_timeout=3)).client
    assert captured["config"].read_timeout == 3
    assert captured["config"].connect_timeout == 3  # never longer than the timeout


# -- the tool boundary: every failure becomes a distinguishable ToolError --
@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (RetrievalAuthError("AWS credentials are missing, expired or invalid"), "failed during authentication"),
        (RetrievalCallError("search timed out after 30s", kind="timeout"), f"{TOOL} failed: search timed out"),
        (RetrievalResultError("search returned a malformed response"), "failed assembling results"),
        (RetrievalInputError("query must be a non-empty string"), f"{TOOL}: invalid input"),
        (RuntimeError(f"unexpected bug near {SECRET}"), "failed unexpectedly"),
    ],
)
async def test_tool_errors_are_attributable_and_summarized(settings, monkeypatch, raised, expected) -> None:
    fake = MagicMock()
    fake.search.side_effect = raised
    with pytest.raises(ToolError) as caught:
        await _call_tool(settings, monkeypatch, fake, {"query": "q"})
    message = str(caught.value)
    assert expected in message
    assert "(trace_id=gen-" in message
    assert SECRET not in message


async def test_failure_is_logged_with_the_same_trace_id_the_client_sees(settings, monkeypatch, traced_caplog) -> None:
    real = _retriever(settings, _client_raising(_client_error("ThrottlingException", 429)))
    with pytest.raises(ToolError) as caught:
        await _call_tool(settings, monkeypatch, real, {"query": "q"})

    trace_id = str(caught.value).split("trace_id=")[1].rstrip(")")
    downstream = next(r for r in traced_caplog.records if r.getMessage() == "Bedrock retrieve failed")
    assert downstream.trace_id == trace_id  # full detail is findable by the id
    assert downstream.aws_message == SECRET
    assert SECRET not in str(caught.value)


# ==========================================================================
# Input validation and schema visibility
# ==========================================================================
async def test_constraints_are_visible_at_the_top_level_of_the_schema(settings) -> None:
    async with Client(server.build_mcp(settings)) as client:
        (tool,) = await client.list_tools()
    schema = tool.inputSchema
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text

    props = schema["properties"]
    assert schema["required"] == ["query"]
    assert props["query"]["minLength"] == 1
    assert props["query"]["maxLength"] == search_tool.MAX_QUERY_LENGTH
    assert props["max_results"]["minimum"] == 1
    assert props["max_results"]["maximum"] == search_tool.MAX_RESULTS
    assert props["max_results"]["default"] == settings.default_max_results


@pytest.mark.parametrize(
    "arguments",
    [
        {},  # missing required query
        {"query": ""},
        {"query": "x" * (search_tool.MAX_QUERY_LENGTH + 1)},
        {"query": "q", "max_results": 0},
        {"query": "q", "max_results": search_tool.MAX_RESULTS + 1},
        {"query": "q", "max_results": "lots"},
        {"query": 42},
    ],
)
async def test_invalid_inputs_are_rejected_before_use(settings, monkeypatch, arguments) -> None:
    fake = MagicMock()
    with pytest.raises(ToolError):
        await _call_tool(settings, monkeypatch, fake, arguments)
    fake.search.assert_not_called()


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"query": "q", "max_results": 99}, "max_results: Input should be less than or equal to 25"),
        ({"query": "q", "max_results": 0}, "max_results: Input should be greater than or equal to 1"),
        ({"query": "q", "max_results": "lots"}, "max_results:"),
        ({"query": ""}, "query: String should have at least 1 character"),
        ({}, "query: Missing required argument"),
    ],
)
async def test_schema_violations_are_summarized_not_raw_pydantic(
    settings, monkeypatch, traced_caplog, arguments, expected
) -> None:
    # fastmcp rejects these before the tool runs and re-raises pydantic's error
    # unchanged; the middleware must turn it into the same summarized ToolError.
    fake = MagicMock()
    with pytest.raises(ToolError) as caught:
        await _call_tool(settings, monkeypatch, fake, arguments)
    message = str(caught.value)

    assert f"{TOOL}: invalid input: " in message
    assert expected in message
    assert "(trace_id=gen-" in message
    # No library internals or docs links leak to the client.
    for internal in ("errors.pydantic.dev", "validation error for call", "input_value", "input_type", "[type="):
        assert internal not in message
    fake.search.assert_not_called()

    # The rejection is logged with the same trace id the client was given.
    trace_id = message.split("trace_id=")[1].rstrip(")")
    record = next(r for r in traced_caplog.records if "rejected by schema validation" in r.getMessage())
    assert record.levelno == logging.WARNING
    assert record.trace_id == trace_id
    assert record.status == "rejected"


async def test_oversized_query_text_is_not_echoed_back(settings, monkeypatch, traced_caplog) -> None:
    marker = "CONFIDENTIAL-MARKER-"
    too_long = marker + "x" * search_tool.MAX_QUERY_LENGTH
    fake = MagicMock()
    with pytest.raises(ToolError) as caught:
        await _call_tool(settings, monkeypatch, fake, {"query": too_long})
    assert marker not in str(caught.value)
    assert "query: String should have at most" in str(caught.value)
    assert marker not in traced_caplog.text  # nor written to the logs


async def test_blank_query_is_rejected_with_a_clear_message(settings, monkeypatch) -> None:
    fake = MagicMock()
    with pytest.raises(ToolError) as caught:
        await _call_tool(settings, monkeypatch, fake, {"query": "   "})
    assert "invalid input: query: query must not be blank" in str(caught.value)
    fake.search.assert_not_called()


async def test_unknown_filter_operator_lists_the_valid_values(settings, monkeypatch) -> None:
    fake = MagicMock()
    with pytest.raises(ToolError) as caught:
        await _call_tool(
            settings, monkeypatch, fake,
            {"query": "q", "metadata_filter": {"like": {"key": "category", "value": "HR"}}},
        )
    message = str(caught.value)
    assert "Unknown filter operator 'like'" in message
    assert "Valid operators: " in message
    for op in ("andAll", "equals", "orAll", "stringContains"):
        assert op in message
    fake.search.assert_not_called()


@pytest.mark.parametrize(
    ("bad_filter", "fragment"),
    [
        ({"equals": {"key": "category"}}, "exactly 'key' and 'value'"),
        ({"equals": {"key": "", "value": "HR"}}, "non-empty string"),
        ({"equals": {"key": "k", "value": {"nested": 1}}}, "string, number or boolean"),
        ({"in": {"key": "k", "value": "not-a-list"}}, "non-empty list"),
        ({"andAll": [{"equals": {"key": "k", "value": 1}}]}, "2 to 5 conditions"),
        ({"equals": {"key": "k", "value": 1}, "notEquals": {"key": "k", "value": 2}}, "exactly one operator"),
        ("not-an-object", "exactly one operator"),
    ],
)
def test_filter_grammar_errors_are_specific(bad_filter, fragment) -> None:
    with pytest.raises(FilterError) as caught:
        validate_filter(bad_filter)
    assert fragment in str(caught.value)


def test_filter_complexity_is_bounded() -> None:
    leaf = {"equals": {"key": "k", "value": 1}}
    deep = leaf
    for _ in range(MAX_DEPTH + 1):
        deep = {"andAll": [deep, leaf]}
    with pytest.raises(FilterError, match="nested at most"):
        validate_filter(deep)

    wide = {"orAll": [{"andAll": [leaf] * 5} for _ in range(5)]}  # 25 conditions
    assert 25 > MAX_CONDITIONS
    with pytest.raises(FilterError, match="at most"):
        validate_filter(wide)


def test_valid_filters_pass_unchanged() -> None:
    flt = {
        "andAll": [
            {"equals": {"key": "category", "value": "HR"}},
            {"in": {"key": "subcategory", "value": ["A", "B"]}},
        ]
    }
    assert validate_filter(flt) is flt
    assert "equals" in VALID_OPERATORS


# -- server-side scope: callers can narrow, never broaden -------------------
SCOPE = {"equals": {"key": "category", "value": "BusinessApps"}}


def _scoped_settings() -> Settings:
    return Settings(kb_id="TESTKB", scope_filter=json.dumps(SCOPE))


def _sent_filter(client: MagicMock):
    cfg = client.retrieve.call_args.kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
    return cfg.get("filter")


def test_scope_is_always_applied() -> None:
    client = _client_returning({"retrievalResults": []})
    _retriever(_scoped_settings(), client).search("q")
    assert _sent_filter(client) == SCOPE


def test_caller_filter_is_anded_with_the_scope() -> None:
    # Even a filter that tries to match "everything" can only narrow the scope.
    broad = {"orAll": [{"equals": {"key": "category", "value": "Finance"}},
                       {"notEquals": {"key": "category", "value": "nothing"}}]}
    client = _client_returning({"retrievalResults": []})
    _retriever(_scoped_settings(), client).search("q", metadata_filter=broad)
    assert _sent_filter(client) == {"andAll": [SCOPE, broad]}


def test_invalid_caller_filter_never_reaches_aws() -> None:
    client = _client_returning({"retrievalResults": []})
    with pytest.raises(RetrievalInputError):
        _retriever(_scoped_settings(), client).search("q", metadata_filter={"drop": "scope"})
    client.retrieve.assert_not_called()


def test_no_scope_and_no_filter_sends_no_filter(settings) -> None:
    client = _client_returning({"retrievalResults": []})
    _retriever(settings, client).search("q")
    assert _sent_filter(client) is None
    assert combine_filters(None, None) is None


@pytest.mark.parametrize("bad_scope", ["{not json", '{"like": {"key": "a", "value": 1}}'])
def test_bad_scope_filter_fails_at_startup(bad_scope) -> None:
    with pytest.raises(ValueError, match="BAMCP_SCOPE_FILTER|Unknown filter operator"):
        Settings(kb_id="TESTKB", scope_filter=bad_scope)


# ==========================================================================
# Output modeling
# ==========================================================================
def test_undeclared_fields_and_raw_locations_are_dropped(settings) -> None:
    resp = _retriever(settings, _client_returning({"retrievalResults": [_hit()]})).search("q")
    (result,) = resp.results

    assert result.source_name == "access.md"
    assert result.source_url is None
    assert result.metadata.model_dump() == {
        "category": "BusinessApps",
        "subcategory": "HR",
        "document_name": "Access guide",
    }

    dumped = resp.model_dump_json()
    for internal in ("s3://", "internal-bucket", "silver/sp", "x-amz-bedrock-kb", "DS123", "chunk-1"):
        assert internal not in dumped


@pytest.mark.parametrize(
    ("location", "name", "url"),
    [
        ({"type": "SHAREPOINT", "sharePointLocation": {"url": "https://pge.sharepoint.com/sites/a/Guide.aspx"}},
         "Guide.aspx", "https://pge.sharepoint.com/sites/a/Guide.aspx"),
        ({"type": "WEB", "webLocation": {"url": "https://example.com/docs/page?x=1"}},
         "page", "https://example.com/docs/page?x=1"),
        ({"type": "S3", "s3Location": {"uri": "s3://bucket/a/b/file.pdf"}}, "file.pdf", None),
        ({"type": "CUSTOM", "customDocumentLocation": {"id": "internal-id-9"}}, None, None),
        ({"type": "UNKNOWN", "weird": {"x": "s3://leak/me"}}, None, None),
        (None, None, None),
    ],
)
def test_only_user_facing_links_are_returned(settings, location, name, url) -> None:
    resp = _retriever(settings, _client_returning({"retrievalResults": [_hit(location=location)]})).search("q")
    (result,) = resp.results
    assert result.source_name == name
    assert result.source_url == url


async def test_malformed_record_becomes_a_safe_tool_error(settings, monkeypatch) -> None:
    bad = _retriever(settings, _client_returning({"retrievalResults": [_hit(score="high")]}))
    with pytest.raises(ToolError) as caught:
        await _call_tool(settings, monkeypatch, bad, {"query": "q"})
    message = str(caught.value)
    assert "failed assembling results: search returned a malformed response" in message
    assert "validation error" not in message.lower()  # no pydantic internals


async def test_successful_response_is_typed_and_clean(settings, monkeypatch) -> None:
    good = _retriever(settings, _client_returning({"retrievalResults": [_hit()]}))
    result = await _call_tool(settings, monkeypatch, good, {"query": "how do I request access"})
    data = result.structured_content
    assert data["result_count"] == 1
    assert set(data["results"][0]) == {"text", "score", "source_name", "source_url", "metadata"}
    assert "s3://" not in json.dumps(data)


# ==========================================================================
# Logging configuration from the environment
# ==========================================================================
def test_log_level_comes_from_log_level_env(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert Settings(kb_id="TESTKB").log_level == "DEBUG"
    monkeypatch.delenv("LOG_LEVEL")
    assert Settings(kb_id="TESTKB").log_level == "INFO"  # default


# ==========================================================================
# Health endpoint and startup logging
# ==========================================================================
def test_startup_log_lists_endpoint_health_url_and_tools(settings, caplog) -> None:
    server.build_mcp(settings)  # registers the tools
    with caplog.at_level(logging.INFO, logger="business_apps_mcp.server"):
        server._log_startup(settings, host="127.0.0.1")
    message = caplog.records[-1].getMessage()
    assert f"endpoint=http://127.0.0.1:{settings.port}/mcp" in message
    assert f"health=http://127.0.0.1:{settings.port}/health" in message
    assert "tools=['search_business_apps']" in message


def test_main_logs_startup_and_serves_with_its_own_logging(settings, monkeypatch, caplog) -> None:
    captured = {}
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "configure_logging", lambda **_k: None)
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kw: captured.update(kw))

    with caplog.at_level(logging.INFO, logger="business_apps_mcp.server"):
        server.main()

    assert captured["host"] == settings.host
    assert captured["port"] == settings.port
    assert captured["access_log"] is False  # the app writes its own access line
    assert captured["log_config"] is None
    assert any("endpoint=http://" in r.getMessage() for r in caplog.records)
