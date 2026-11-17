"""Shared pytest fixtures."""

from __future__ import annotations

import os

import pytest

from business_apps_mcp.config import Settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep tests deterministic regardless of the caller's environment.

    pydantic-settings lets both ``BAMCP_*`` environment variables AND a local
    ``.env`` file override explicit ``Settings(...)`` kwargs. During tests we
    want the constructor args to be authoritative, so:

    * strip any ``BAMCP_*`` env vars (e.g. left over from running the server), and
    * disable ``.env`` loading (a real .env exists for local dev/testing).
    """
    for key in list(os.environ):
        if key.startswith("BAMCP_"):
            monkeypatch.delenv(key, raising=False)

    # Neutralize .env loading so an on-disk .env can't change test behavior.
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture
def settings() -> Settings:
    """Deterministic settings for tests (auth on, reranking off)."""
    return Settings(
        aws_region="us-west-2",
        kb_id="TESTKB0001",
        default_max_results=5,
        max_results_limit=25,
        reranking_enabled=False,
        api_keys="secret-key-1,secret-key-2",
        log_format="console",
    )


@pytest.fixture
def sample_retrieve_response() -> dict:
    """A representative bedrock-agent-runtime.retrieve() response."""
    return {
        "retrievalResults": [
            {
                "content": {"text": "Business app onboarding steps..."},
                "score": 0.82,
                "location": {"type": "S3", "s3Location": {"uri": "s3://bucket/business-apps/doc1.md"}},
                "metadata": {"x-amz-bedrock-kb-document-id": "doc-1", "subcategory": "HR"},
            },
            {
                "content": {"text": "Access request workflow..."},
                "score": 0.77,
                "location": {
                    "type": "SHAREPOINT",
                    "sharePointLocation": {"url": "https://pge.sharepoint.com/page2"},
                },
                "metadata": {"x-amz-bedrock-kb-chunk-id": "chunk-9"},
            },
        ]
    }
