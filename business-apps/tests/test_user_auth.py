"""Tests for Cognito user authentication (mirrors the backend's JWT flow).

The JWKS fetch and JWT signature verification are mocked so these stay fast,
offline unit tests. The focus is on the control flow that matters for parity
with the backend: bypass, header handling, access-token enforcement, userInfo
merge, and the always-present ``Default`` group.
"""

from __future__ import annotations

import pytest

from business_apps_mcp.config import Settings
from business_apps_mcp.user_auth import (
    CUSTOM_GROUPS_KEY,
    CognitoUserAuthenticator,
    UserAuthError,
    _ensure_custom_groups_with_default,
    _parse_bracketed_list,
)


def _settings(**overrides) -> Settings:
    base = dict(
        aws_region="us-west-2",
        kb_id="TESTKB",
        cognito_user_pool_id="TESTPOOL",
        cognito_app_client_id="test-client-id",
        cognito_user_info_url="https://example.auth.us-west-2.amazoncognito.com/oauth2/userInfo",
        user_auth_enabled=True,
    )
    base.update(overrides)
    return Settings(**base)


# --- pure helpers ---------------------------------------------------------
def test_parse_bracketed_list() -> None:
    assert _parse_bracketed_list("[a, b, c]") == ["a", "b", "c"]
    assert _parse_bracketed_list("[single]") == ["single"]
    assert _parse_bracketed_list("[]") == []


@pytest.mark.parametrize(
    ("initial", "expected_tail"),
    [
        ({}, ["Default"]),
        ({CUSTOM_GROUPS_KEY: ["G1"]}, ["G1", "Default"]),
        ({CUSTOM_GROUPS_KEY: "[G1, G2]"}, ["G1", "G2", "Default"]),
        ({CUSTOM_GROUPS_KEY: 123}, ["Default"]),  # bad type -> default only
    ],
)
def test_ensure_default_group(initial, expected_tail) -> None:
    _ensure_custom_groups_with_default(initial)
    assert initial[CUSTOM_GROUPS_KEY] == expected_tail


def test_derived_issuer_and_jwks_url() -> None:
    s = _settings()
    assert s.cognito_issuer == (
        "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_TESTPOOL"
    )
    assert s.cognito_jwks_url.endswith("/.well-known/jwks.json")


# --- header handling ------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_header_rejected() -> None:
    auth = CognitoUserAuthenticator(_settings())
    with pytest.raises(UserAuthError):
        await auth.verify(None)


@pytest.mark.asyncio
async def test_non_bearer_header_rejected() -> None:
    auth = CognitoUserAuthenticator(_settings())
    with pytest.raises(UserAuthError):
        await auth.verify("Basic abc123")


# --- bypass ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_bypass_token_returns_test_user() -> None:
    auth = CognitoUserAuthenticator(_settings(bypass_secret="dev-secret"))
    claims = await auth.verify("Bearer dev-secret")
    assert claims["username"] == "test"
    assert claims[CUSTOM_GROUPS_KEY] == ["Default"]


@pytest.mark.asyncio
async def test_bypass_disabled_when_empty() -> None:
    # Empty bypass secret must never match an empty-ish token.
    auth = CognitoUserAuthenticator(_settings(bypass_secret=""))
    with pytest.raises(UserAuthError):
        await auth.verify("Bearer ")


# --- full verification path (mocked jwt + jwks + userInfo) ----------------
@pytest.mark.asyncio
async def test_valid_access_token_merges_userinfo(monkeypatch) -> None:
    auth = CognitoUserAuthenticator(_settings())

    # Mock the signing-key lookup and signature verification.
    async def fake_get_key(_kid):
        return {"kid": "abc", "alg": "RS256"}

    monkeypatch.setattr(auth._jwks, "get_key", fake_get_key)
    monkeypatch.setattr(
        "business_apps_mcp.user_auth.jwt.get_unverified_header",
        lambda _t: {"kid": "abc"},
    )
    monkeypatch.setattr(
        "business_apps_mcp.user_auth.jwt.decode",
        lambda *a, **k: {"token_use": "access", "username": "alice"},
    )

    async def fake_user_info(_token):
        return {
            "email": "alice@pge.com",
            "given_name": "Alice",
            "custom:groups": ["Grp-A"],
        }

    monkeypatch.setattr(auth, "_fetch_user_info", fake_user_info)

    claims = await auth.verify("Bearer sometoken")
    assert claims["username"] == "alice"
    assert claims["email"] == "alice@pge.com"
    assert claims[CUSTOM_GROUPS_KEY] == ["Grp-A", "Default"]


@pytest.mark.asyncio
async def test_id_token_rejected(monkeypatch) -> None:
    auth = CognitoUserAuthenticator(_settings())

    async def fake_get_key(_kid):
        return {"kid": "abc", "alg": "RS256"}

    monkeypatch.setattr(auth._jwks, "get_key", fake_get_key)
    monkeypatch.setattr(
        "business_apps_mcp.user_auth.jwt.get_unverified_header",
        lambda _t: {"kid": "abc"},
    )
    # token_use is "id" -> must be rejected.
    monkeypatch.setattr(
        "business_apps_mcp.user_auth.jwt.decode",
        lambda *a, **k: {"token_use": "id"},
    )

    with pytest.raises(UserAuthError) as exc:
        await auth.verify("Bearer sometoken")
    assert "access token" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_missing_kid_rejected(monkeypatch) -> None:
    auth = CognitoUserAuthenticator(_settings())
    monkeypatch.setattr(
        "business_apps_mcp.user_auth.jwt.get_unverified_header",
        lambda _t: {},  # no kid
    )
    with pytest.raises(UserAuthError):
        await auth.verify("Bearer sometoken")


@pytest.mark.asyncio
async def test_unknown_kid_rejected(monkeypatch) -> None:
    auth = CognitoUserAuthenticator(_settings())

    async def fake_get_key(_kid):
        return None  # key not found even after rotation retry

    monkeypatch.setattr(auth._jwks, "get_key", fake_get_key)
    monkeypatch.setattr(
        "business_apps_mcp.user_auth.jwt.get_unverified_header",
        lambda _t: {"kid": "missing"},
    )
    with pytest.raises(UserAuthError):
        await auth.verify("Bearer sometoken")
