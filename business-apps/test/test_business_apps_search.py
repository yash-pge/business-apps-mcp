"""Unit tests for tools.business_apps_search.search_business_apps_vectordb (HTTP is mocked)."""

from types import SimpleNamespace

import pytest
import requests

from tools import business_apps_search


class FakeResponse:
    def __init__(self, payload=None, *, json_error=False, http_error=None):
        self._payload = payload
        self._json_error = json_error
        self._http_error = http_error

    def raise_for_status(self):
        if self._http_error:
            raise self._http_error

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def captured(monkeypatch):
    """Patch requests.post and record the call; returns the capture dict."""
    monkeypatch.setenv("API_ENDPOINT", "https://api.example.com/retrieve")
    monkeypatch.setenv("RERANKING_MODEL_ARN", "arn:test:rerank")
    monkeypatch.delenv("API_REQUEST_TIMEOUT", raising=False)
    calls = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.update(url=url, json=json, headers=headers, timeout=timeout)
        return calls["response"]

    calls["response"] = FakeResponse({"retrievalResults": [{"id": 1}]})
    monkeypatch.setattr(business_apps_search.requests, "post", fake_post)
    return calls


def test_success_builds_payload_and_returns_results(captured):
    results = business_apps_search.search_business_apps_vectordb("kb-1", "tok", "hello", 3)

    assert results == [{"id": 1}]
    assert captured["url"] == "https://api.example.com/retrieve"
    assert captured["timeout"] == 30
    assert captured["headers"]["Authorization"] == "Bearer tok"
    body = captured["json"]
    assert body["knowledgeBaseId"] == "kb-1"
    assert body["retrievalQuery"] == {"text": "hello"}
    config = body["retrievalConfiguration"]["vectorSearchConfiguration"]
    assert config["numberOfResults"] == 3
    assert "rerankingConfiguration" not in config
    assert "filter" not in config


def test_rerank_and_filter_are_added(captured):
    business_apps_search.search_business_apps_vectordb(
        "kb-1", "tok", "q", rerank=True, metadata_filter={"equals": {"k": "v"}}
    )

    config = captured["json"]["retrievalConfiguration"]["vectorSearchConfiguration"]
    rerank = config["rerankingConfiguration"]["bedrockRerankingConfiguration"]
    assert rerank["modelConfiguration"]["modelArn"] == "arn:test:rerank"
    assert config["filter"] == {"equals": {"k": "v"}}


def test_timeout_env_override(captured, monkeypatch):
    monkeypatch.setenv("API_REQUEST_TIMEOUT", "7")
    business_apps_search.search_business_apps_vectordb("kb", "tok", "q")
    assert captured["timeout"] == 7


def test_missing_results_key_returns_empty_list(captured):
    captured["response"] = FakeResponse({})
    assert business_apps_search.search_business_apps_vectordb("kb", "tok", "q") == []


def test_timeout_raises_runtime_error(monkeypatch, captured):
    def boom(*args, **kwargs):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr(business_apps_search.requests, "post", boom)
    with pytest.raises(RuntimeError, match="timed out after 30s"):
        business_apps_search.search_business_apps_vectordb("kb", "tok", "q")


@pytest.mark.parametrize(
    "response, expected",
    [(SimpleNamespace(status_code=503), "HTTP 503"), (None, "HTTP unknown")],
)
def test_http_error_reports_status(captured, response, expected):
    error = requests.exceptions.HTTPError(response=response)
    captured["response"] = FakeResponse(http_error=error)
    with pytest.raises(RuntimeError, match=expected):
        business_apps_search.search_business_apps_vectordb("kb", "tok", "q")


def test_connection_error_raises_runtime_error(monkeypatch, captured):
    def boom(*args, **kwargs):
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(business_apps_search.requests, "post", boom)
    with pytest.raises(RuntimeError, match="Could not reach"):
        business_apps_search.search_business_apps_vectordb("kb", "tok", "q")


def test_non_json_response_raises_runtime_error(captured):
    captured["response"] = FakeResponse(json_error=True)
    with pytest.raises(RuntimeError, match="malformed"):
        business_apps_search.search_business_apps_vectordb("kb", "tok", "q")
