"""Tests for the API-key / identity-forwarding middleware."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from business_apps_mcp.auth import ApiKeyAuthMiddleware
from business_apps_mcp.config import Settings


def _build_app(settings: Settings) -> Starlette:
    async def echo(request: Request) -> JSONResponse:
        return JSONResponse({"identity": getattr(request.state, "user_identity", None)})

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[Route("/mcp", echo), Route("/health", health)])
    app.add_middleware(ApiKeyAuthMiddleware, settings=settings)
    return app


def test_health_is_public() -> None:
    settings = Settings(kb_id="TESTKB", api_keys="k1")
    client = TestClient(_build_app(settings))
    resp = client.get("/health")
    assert resp.status_code == 200


def test_missing_key_rejected() -> None:
    settings = Settings(kb_id="TESTKB", api_keys="k1")
    client = TestClient(_build_app(settings))
    resp = client.get("/mcp")
    assert resp.status_code == 401


def test_wrong_key_rejected() -> None:
    settings = Settings(kb_id="TESTKB", api_keys="k1")
    client = TestClient(_build_app(settings))
    resp = client.get("/mcp", headers={"x-api-key": "nope"})
    assert resp.status_code == 401


def test_valid_key_allowed_and_identity_forwarded() -> None:
    settings = Settings(kb_id="TESTKB", api_keys="k1", identity_header="x-forwarded-user")
    client = TestClient(_build_app(settings))
    resp = client.get("/mcp", headers={"x-api-key": "k1", "x-forwarded-user": "alice@pge.com"})
    assert resp.status_code == 200
    assert resp.json()["identity"] == "alice@pge.com"


def test_auth_disabled_allows_all() -> None:
    settings = Settings(kb_id="TESTKB", api_keys="")  # auth disabled
    client = TestClient(_build_app(settings))
    resp = client.get("/mcp")
    assert resp.status_code == 200
    assert resp.json()["identity"] == "anonymous"


# --- user (Cognito) auth layer in the middleware --------------------------
def _build_user_auth_app(settings: Settings, authenticator) -> Starlette:
    async def echo(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "identity": getattr(request.state, "user_identity", None),
                "groups": getattr(request.state, "claims", {}).get("custom:groups"),
            }
        )

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[Route("/mcp", echo), Route("/health", health)])
    app.add_middleware(
        ApiKeyAuthMiddleware, settings=settings, authenticator=authenticator
    )
    return app


class _StubAuthenticator:
    """Minimal stand-in for CognitoUserAuthenticator used by the middleware."""

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    async def verify(self, _authorization):
        if self._error:
            raise self._error
        return self._result


def test_user_auth_rejects_bad_token() -> None:
    from business_apps_mcp.user_auth import UserAuthError

    settings = Settings(kb_id="TESTKB", api_keys="", user_auth_enabled=True)
    stub = _StubAuthenticator(error=UserAuthError("Token verification failed"))
    client = TestClient(_build_user_auth_app(settings, stub))

    resp = client.get("/mcp", headers={"authorization": "Bearer bad"})
    assert resp.status_code == 401


def test_user_auth_sets_identity_and_groups() -> None:
    settings = Settings(kb_id="TESTKB", api_keys="", user_auth_enabled=True)
    stub = _StubAuthenticator(
        result={"username": "bob@pge.com", "custom:groups": ["G1", "Default"]}
    )
    client = TestClient(_build_user_auth_app(settings, stub))

    resp = client.get("/mcp", headers={"authorization": "Bearer good"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["identity"] == "bob@pge.com"
    assert body["groups"] == ["G1", "Default"]


def test_health_public_even_with_user_auth() -> None:
    settings = Settings(kb_id="TESTKB", api_keys="k1", user_auth_enabled=True)
    stub = _StubAuthenticator(error=AssertionError("should not be called"))
    client = TestClient(_build_user_auth_app(settings, stub))
    resp = client.get("/health")
    assert resp.status_code == 200


# --- authorization layer: who may USE the server --------------------------
def test_authorized_group_allowed() -> None:
    # User is in an allowed group -> 200.
    settings = Settings(
        kb_id="TESTKB",
        api_keys="",
        user_auth_enabled=True,
        allowed_groups="BusinessApps-Access, Admins",
    )
    stub = _StubAuthenticator(
        result={"username": "ok@pge.com", "custom:groups": ["BusinessApps-Access", "Default"]}
    )
    client = TestClient(_build_user_auth_app(settings, stub))
    resp = client.get("/mcp", headers={"authorization": "Bearer good"})
    assert resp.status_code == 200
    assert resp.json()["identity"] == "ok@pge.com"


def test_unauthorized_group_forbidden() -> None:
    # Authenticated but NOT in any allowed group -> 403 (not 401).
    settings = Settings(
        kb_id="TESTKB",
        api_keys="",
        user_auth_enabled=True,
        allowed_groups="BusinessApps-Access",
    )
    stub = _StubAuthenticator(
        result={"username": "nope@pge.com", "custom:groups": ["SomeOtherGroup", "Default"]}
    )
    client = TestClient(_build_user_auth_app(settings, stub))
    resp = client.get("/mcp", headers={"authorization": "Bearer good"})
    assert resp.status_code == 403
    assert resp.json()["error"] == "forbidden"


def test_no_allowlist_allows_any_authenticated_user() -> None:
    # Empty allow-list -> authorization off -> any authenticated user passes.
    settings = Settings(
        kb_id="TESTKB", api_keys="", user_auth_enabled=True, allowed_groups=""
    )
    stub = _StubAuthenticator(
        result={"username": "any@pge.com", "custom:groups": ["Whatever", "Default"]}
    )
    client = TestClient(_build_user_auth_app(settings, stub))
    resp = client.get("/mcp", headers={"authorization": "Bearer good"})
    assert resp.status_code == 200


def test_authorization_handles_bracketed_string_groups() -> None:
    # custom:groups delivered as a bracketed string is parsed correctly.
    settings = Settings(
        kb_id="TESTKB",
        api_keys="",
        user_auth_enabled=True,
        allowed_groups="G2",
    )
    stub = _StubAuthenticator(
        result={"username": "s@pge.com", "custom:groups": "[G1, G2, Default]"}
    )
    client = TestClient(_build_user_auth_app(settings, stub))
    resp = client.get("/mcp", headers={"authorization": "Bearer good"})
    assert resp.status_code == 200
