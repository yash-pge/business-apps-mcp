"""Cognito user authentication (mirrors the Amplify backend).

This verifies an end-user's Cognito **access token** on each request, exactly
like ``Amplify-RAG-Backend/app/src/api/authentication.py`` does:

1. Read the ``Authorization: Bearer <jwt>`` header.
2. (Dev only) If the token equals the configured bypass secret, skip
   verification and return a synthetic test user.
3. Decode the JWT header, find the matching public key in the pool's JWKS
   (refresh once on a cache miss to survive key rotation).
4. Verify the signature, issuer, audience (app client id) and expiry.
5. Enforce ``token_use == "access"``.
6. Fetch the user's profile + groups from the Cognito ``userInfo`` endpoint.
7. Ensure ``custom:groups`` always exists and contains ``"Default"``.

This is the *user* identity layer. It is independent of AWS authentication
(the boto3 credentials the retriever uses to call Bedrock) and of the
gateway API-key check in ``auth.py``.
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx
from jose import jwt

from .config import Settings
from .logging_config import get_logger

logger = get_logger(__name__)

CUSTOM_GROUPS_KEY = "custom:groups"


class UserAuthError(Exception):
    """Raised when a Cognito access token cannot be verified.

    ``status_code`` is 401 for all auth failures (mirrors the backend).
    """

    def __init__(self, detail: str, status_code: int = 401) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def _parse_bracketed_list(value: str) -> list[str]:
    """Parse a ``"[a, b, c]"`` style string into a list of non-empty items."""
    content = re.sub(r"[\[\]]", "", value.strip())
    if not content:
        return []
    return [item.strip() for item in content.split(",") if item.strip()]


def _ensure_custom_groups_with_default(claims: dict[str, Any]) -> None:
    """Ensure ``custom:groups`` exists and always contains ``"Default"``.

    Matches the backend so downstream group checks have a predictable shape.
    """
    if CUSTOM_GROUPS_KEY not in claims:
        claims[CUSTOM_GROUPS_KEY] = ["Default"]
        return

    groups = claims[CUSTOM_GROUPS_KEY]
    if isinstance(groups, list):
        groups.append("Default")
    elif isinstance(groups, str):
        claims[CUSTOM_GROUPS_KEY] = _parse_bracketed_list(groups)
        claims[CUSTOM_GROUPS_KEY].append("Default")
    else:
        logger.warning("custom:groups is not a list or string; defaulting to ['Default'].")
        claims[CUSTOM_GROUPS_KEY] = ["Default"]


class JWKSCache:
    """Caches the pool's JSON Web Key Set with a TTL (like the backend)."""

    def __init__(self, url: str, ttl_seconds: int, verify_ssl: bool = True) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._verify_ssl = verify_ssl
        self._jwks: dict[str, Any] | None = None
        self._expires_at: float = 0.0

    async def get(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if force or not self._jwks or now >= self._expires_at:
            async with httpx.AsyncClient(timeout=10, verify=self._verify_ssl) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
                self._jwks = resp.json()
                self._expires_at = now + self._ttl
        return self._jwks

    async def get_key(self, kid: str) -> dict[str, Any] | None:
        """Return the JWK matching ``kid``, refreshing once on a miss."""
        jwks = await self.get()
        key = self._find(jwks, kid)
        if key is None:
            # Possible key rotation: force a refresh and try once more.
            jwks = await self.get(force=True)
            key = self._find(jwks, kid)
        return key

    @staticmethod
    def _find(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
        return next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)


class CognitoUserAuthenticator:
    """Verifies Cognito access tokens and returns normalized user claims."""

    def __init__(self, settings: Settings, jwks_cache: JWKSCache | None = None) -> None:
        self._settings = settings
        self._jwks = jwks_cache or JWKSCache(
            settings.cognito_jwks_url,
            settings.jwks_ttl_seconds,
            settings.verify_ssl,
        )

    async def verify(self, authorization: str | None) -> dict[str, Any]:
        """Verify the ``Authorization`` header and return user claims.

        Raises :class:`UserAuthError` (401) on any failure.
        """
        if not authorization or not authorization.lower().startswith("bearer "):
            raise UserAuthError("Invalid authorization header format.")

        token = authorization.split(" ", 1)[1].strip()

        # 1) Dev bypass (matches the backend's BYPASS_SECRET shortcut).
        if self._settings.bypass_secret and token == self._settings.bypass_secret:
            logger.warning("User auth BYPASS token accepted — local/dev only.")
            claims: dict[str, Any] = {"username": "test"}
            _ensure_custom_groups_with_default(claims)
            return claims

        # 2) Decode header to find the signing key id.
        try:
            header = jwt.get_unverified_header(token)
        except Exception as exc:  # noqa: BLE001 - normalize to 401
            raise UserAuthError(f"Invalid token header: {exc}") from exc

        kid = header.get("kid")
        if not kid:
            raise UserAuthError("Token header missing 'kid'.")

        # 3) Find the matching JWK (with a rotation retry).
        key = await self._jwks.get_key(kid)
        if key is None:
            raise UserAuthError("Public key not found in JWKS.")

        # 4) Verify signature, issuer, audience and expiry.
        try:
            verified = jwt.decode(
                token,
                key,
                algorithms=[key.get("alg", "RS256")],
                audience=self._settings.cognito_app_client_id,
                issuer=self._settings.cognito_issuer,
                options={"verify_at_hash": False},
            )
        except Exception as exc:  # noqa: BLE001 - normalize to 401
            raise UserAuthError(f"Token verification failed: {exc}") from exc

        # 5) Enforce access token (not an id token).
        if verified.get("token_use") != "access":
            raise UserAuthError("Expected an access token.")

        # 6) Fetch profile + groups from Cognito userInfo.
        user_info = await self._fetch_user_info(token)

        # 7) Combine claims (mirrors the backend's shape).
        claims = {
            "username": verified.get("username") or user_info.get("username"),
            "email": user_info.get("email"),
            "given_name": user_info.get("given_name"),
            "family_name": user_info.get("family_name"),
            "name": user_info.get("name"),
            **{k: v for k, v in user_info.items() if k.startswith("custom:")},
        }
        _ensure_custom_groups_with_default(claims)
        return claims

    async def _fetch_user_info(self, token: str) -> dict[str, Any]:
        """Call Cognito's ``/oauth2/userInfo`` with the bearer token."""
        url = self._settings.cognito_user_info_url
        try:
            async with httpx.AsyncClient(timeout=10, verify=self._settings.verify_ssl) as client:
                resp = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "userInfo request failed",
                extra={"status": exc.response.status_code},
            )
            raise UserAuthError(
                f"Failed to fetch user information: {exc.response.status_code}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - normalize to 401
            logger.error("userInfo request error", extra={"error": str(exc)})
            raise UserAuthError(f"Error fetching user information: {exc}") from exc
