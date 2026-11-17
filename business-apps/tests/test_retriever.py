"""Tests for the KnowledgeBaseRetriever (boto client mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from business_apps_mcp.retriever import KnowledgeBaseRetriever, RetrievalError


def _make_retriever(settings, fake_client):
    r = KnowledgeBaseRetriever(settings=settings)
    r._client = fake_client  # inject mock, bypass lazy boto init
    return r


def test_search_parses_results(settings, sample_retrieve_response) -> None:
    client = MagicMock()
    client.retrieve.return_value = sample_retrieve_response
    r = _make_retriever(settings, client)

    resp = r.search("how do I onboard a business app?")

    assert resp.result_count == 2
    assert resp.reranked is False
    assert resp.query == "how do I onboard a business app?"

    first, second = resp.results
    assert first.text.startswith("Business app onboarding")
    assert first.score == 0.82
    # S3 source: only the file name is returned, never the bucket/key path.
    assert first.source_name == "doc1.md"
    assert first.source_url is None
    assert first.metadata.subcategory == "HR"

    # SharePoint source: the user-facing link is kept.
    assert second.source_url == "https://pge.sharepoint.com/page2"
    assert second.source_name == "page2"

    # Internal bookkeeping and raw storage locations are dropped from the output.
    dumped = resp.model_dump_json()
    assert "s3://" not in dumped
    assert "x-amz-bedrock-kb" not in dumped
    assert "doc-1" not in dumped and "chunk-9" not in dumped


def test_search_respects_max_results_clamp(settings) -> None:
    client = MagicMock()
    client.retrieve.return_value = {"retrievalResults": []}
    r = _make_retriever(settings, client)

    r.search("q", max_results=999)

    kwargs = client.retrieve.call_args.kwargs
    vector_cfg = kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]
    assert vector_cfg["numberOfResults"] == settings.max_results_limit
    assert "filter" not in vector_cfg
    assert "rerankingConfiguration" not in vector_cfg  # off by default


def test_search_passes_metadata_filter(settings) -> None:
    client = MagicMock()
    client.retrieve.return_value = {"retrievalResults": []}
    r = _make_retriever(settings, client)

    flt = {"equals": {"key": "subcategory", "value": "HR"}}
    r.search("q", metadata_filter=flt)

    vector_cfg = client.retrieve.call_args.kwargs["retrievalConfiguration"][
        "vectorSearchConfiguration"
    ]
    assert vector_cfg["filter"] == flt


def test_reranking_attached_when_enabled(settings) -> None:
    settings.reranking_enabled = True
    client = MagicMock()
    client.retrieve.return_value = {"retrievalResults": []}
    r = _make_retriever(settings, client)

    resp = r.search("q")

    vector_cfg = client.retrieve.call_args.kwargs["retrievalConfiguration"][
        "vectorSearchConfiguration"
    ]
    rerank_cfg = vector_cfg["rerankingConfiguration"]
    # Shape must match the Bedrock VectorSearchRerankingConfiguration contract:
    # a type discriminator + a single matching nested model config (no double nesting).
    assert rerank_cfg["type"] == "BEDROCK_RERANKING_MODEL"
    assert "rerankingConfiguration" not in rerank_cfg
    bedrock_cfg = rerank_cfg["bedrockRerankingConfiguration"]
    assert bedrock_cfg["modelConfiguration"]["modelArn"].endswith(settings.reranking_model_id)
    # No max_results passed, so it defaults to default_max_results.
    assert bedrock_cfg["numberOfRerankedResults"] == settings.default_max_results
    assert resp.reranked is True


def test_empty_query_raises(settings) -> None:
    r = _make_retriever(settings, MagicMock())
    with pytest.raises(RetrievalError):
        r.search("   ")


def test_client_error_wrapped(settings) -> None:
    client = MagicMock()
    client.retrieve.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Retrieve"
    )
    r = _make_retriever(settings, client)
    with pytest.raises(RetrievalError) as exc:
        r.search("q")
    assert "AccessDeniedException" in str(exc.value)
