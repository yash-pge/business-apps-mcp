"""
Shared pytest fixtures and helpers for the business-apps MCP server test suite.

Same helpers as ecm-mcp-server: keeps the tools package importable without a
real `.env` or network access by providing lightweight fakes for the inbound
HTTP request context and a way to capture the tool closure that `register(mcp)`
decorates.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Make the project root importable (so `import tools...` works) regardless of the
# directory pytest is invoked from.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Variables the code reads. They are cleared per test so a developer's real
# .env / shell never changes test behavior.
_ENV_VARS = (
    "ENTRA_TENANT_ID", "ENTRA_CLIENT_ID", "ENTRA_SCOPE",
    "CERTIFICATE_DATA", "CERTIFICATE_PASSWORD",
    "API_ENDPOINT", "API_REQUEST_TIMEOUT", "KB_ID", "RERANK", "RERANKING_MODEL_ARN",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class FakeHeaders:
    """Case-insensitive header lookup mimicking Starlette's Headers.get()."""

    def __init__(self, data: dict | None = None):
        self._data = {k.lower(): v for k, v in (data or {}).items()}

    def get(self, key, default=None):
        return self._data.get(key.lower(), default)


def make_ctx(headers: dict | None = None, *, broken: bool = False):
    """Build a minimal object shaped like a FastMCP Context."""
    if broken:
        class _Broken:
            @property
            def request_context(self):
                raise AttributeError("no request context")

        return _Broken()

    request = SimpleNamespace(
        headers=FakeHeaders(headers) if headers is not None else None
    )
    request_context = SimpleNamespace(request=request)
    return SimpleNamespace(request_context=request_context)


class FakeMCP:
    """Captures the function registered via the @mcp.tool(...) decorator."""

    def __init__(self):
        self.registered = {}

    def tool(self, name=None, description=None):
        def decorator(fn):
            self.registered[name] = fn
            return fn

        return decorator


@pytest.fixture
def fake_mcp():
    return FakeMCP()


@pytest.fixture
def search_tool(fake_mcp):
    """Register search_business_apps against a fake MCP and return a caller."""
    from tools import mcp_search_business_apps as module

    module.register(fake_mcp)
    handler = fake_mcp.registered[module.TOOL_NAME]

    def call(query="q", max_results=5, ctx=None):
        return handler(query, ctx if ctx is not None else make_ctx(), max_results)

    return call
