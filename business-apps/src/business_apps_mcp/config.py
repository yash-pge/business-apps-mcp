"""Centralized, validated configuration loaded from the environment.

All settings use the ``BAMCP_`` prefix (see ``.env.example``). Loading happens
once via the cached :func:`get_settings` accessor so the rest of the codebase
never reads ``os.environ`` directly.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .filters import FilterError, validate_filter


class Settings(BaseSettings):
    """Typed application settings sourced from environment variables / .env.

    Most settings use the ``BAMCP_`` prefix. Two also accept the unprefixed names
    used by ecm-mcp-server, so the same container environment works for both:
    ``LOG_LEVEL`` and ``API_REQUEST_TIMEOUT``.
    """

    model_config = SettingsConfigDict(
        env_prefix="BAMCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # Lets code and tests pass ``Settings(log_level=...)`` for fields that
        # also declare environment aliases.
        populate_by_name=True,
    )

    # --- AWS / Bedrock Knowledge Base -------------------------------------
    aws_region: str = Field(default="us-west-2", description="AWS region of the KB.")
    kb_id: str = Field(
        default="",
        description="Bedrock Knowledge Base ID (dev). Required — set BAMCP_KB_ID.",
    )
    default_max_results: int = Field(default=5, ge=1, le=100)
    max_results_limit: int = Field(default=25, ge=1, le=100)

    # Seconds to wait on the Knowledge Base call before giving up (read timeout).
    # Same name as ecm-mcp-server's API_REQUEST_TIMEOUT, default 30s. Note the AWS
    # SDK retries throttled/transient failures, so the worst case is longer.
    request_timeout: int = Field(
        default=30,
        ge=1,
        le=300,
        validation_alias=AliasChoices("BAMCP_REQUEST_TIMEOUT", "API_REQUEST_TIMEOUT"),
        description="Timeout in seconds for the Knowledge Base call.",
    )

    # Server-side scope: a Bedrock retrieval filter (JSON) that is ALWAYS applied
    # and ANDed with any filter the caller sends, so callers can narrow results
    # but never broaden them. Empty = the whole Knowledge Base is in scope.
    scope_filter: str = Field(
        default="",
        description="JSON Bedrock retrieval filter always applied server-side (empty = none).",
    )

    # --- Reranking (off for v1) -------------------------------------------
    reranking_enabled: bool = Field(default=False)
    reranking_model_id: str = Field(default="cohere.rerank-v3-5:0")

    # --- HTTP server ------------------------------------------------------
    # Loopback by default so a bare `python -m business_apps_mcp.server` is never
    # exposed on every interface (same choice as ecm-mcp-server). The container
    # starts uvicorn with an explicit --host 0.0.0.0.
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8080, ge=1, le=65535)
    mcp_path: str = Field(default="/mcp")

    # --- Gateway / API-key auth -------------------------------------------
    api_keys: str = Field(default="", description="Comma-separated accepted API keys.")
    api_key_header: str = Field(default="x-api-key")
    identity_header: str = Field(default="x-forwarded-user")

    # --- User auth (Cognito JWT, mirrors the Amplify backend) -------------
    # When enabled, every non-public request must carry a valid Cognito access
    # token in the Authorization header (``Bearer <jwt>``). The token is verified
    # against the user pool's JWKS exactly like the backend does.
    user_auth_enabled: bool = Field(
        default=False,
        description="Require a valid Cognito access token on every request.",
    )
    cognito_user_pool_id: str = Field(
        default="",
        description="Cognito user pool id suffix (the part after '<region>_'). Required when user auth is enabled.",
    )
    cognito_app_client_id: str = Field(
        default="",
        description="Cognito app client id; verified as the token audience. Required when user auth is enabled.",
    )
    cognito_user_info_url: str = Field(
        default="",
        description="Cognito Hosted UI userInfo endpoint (groups/profile source). Required when user auth is enabled.",
    )
    jwks_ttl_seconds: int = Field(default=3600, ge=60, description="JWKS cache TTL.")
    verify_ssl: bool = Field(
        default=True,
        description="Verify TLS on JWKS/userInfo calls. Only disable for local dev.",
    )
    # Dev-only shortcut matching the backend's BYPASS_SECRET: when the bearer
    # token equals this value, verification is skipped and a test user is used.
    bypass_secret: str = Field(default="", description="Dev bypass token (empty = off).")

    # --- Authorization (who may USE this MCP server) ----------------------
    # Comma-separated Cognito groups (custom:groups) allowed to use the server.
    # Empty = any authenticated user is allowed (authorization check off).
    # When set, a user must belong to AT LEAST ONE of these groups or they get
    # 403 Forbidden — this is the "does this user have access" gate.
    allowed_groups: str = Field(
        default="",
        description="Comma-separated groups allowed to use the server (empty = allow all authenticated users).",
    )

    @property
    def allowed_group_set(self) -> frozenset[str]:
        """Parsed set of groups permitted to use the server."""
        return frozenset(g.strip() for g in self.allowed_groups.split(",") if g.strip())

    @property
    def authorization_enabled(self) -> bool:
        """True when an allow-list is configured (group membership is enforced)."""
        return len(self.allowed_group_set) > 0

    @property
    def cognito_issuer(self) -> str:
        """The expected token issuer URL for this user pool."""
        return (
            f"https://cognito-idp.{self.aws_region}.amazonaws.com/"
            f"{self.aws_region}_{self.cognito_user_pool_id}"
        )

    @property
    def cognito_jwks_url(self) -> str:
        """Public JWKS endpoint used to verify token signatures."""
        return f"{self.cognito_issuer}/.well-known/jwks.json"

    # --- Logging ----------------------------------------------------------
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("BAMCP_LOG_LEVEL", "LOG_LEVEL"),
    )
    log_format: str = Field(default="json", description="json | console")

    @field_validator("kb_id")
    @classmethod
    def _require_kb_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "BAMCP_KB_ID is required — set it to the dev Business Apps "
                "Knowledge Base ID (no prod default is shipped)."
            )
        return value.strip()

    @model_validator(mode="after")
    def _require_cognito_when_user_auth(self) -> "Settings":
        if self.user_auth_enabled:
            missing = [
                name
                for name, val in (
                    ("BAMCP_COGNITO_USER_POOL_ID", self.cognito_user_pool_id),
                    ("BAMCP_COGNITO_APP_CLIENT_ID", self.cognito_app_client_id),
                    ("BAMCP_COGNITO_USER_INFO_URL", self.cognito_user_info_url),
                )
                if not val.strip()
            ]
            if missing:
                raise ValueError(
                    "BAMCP_USER_AUTH_ENABLED=true requires: " + ", ".join(missing)
                )
        return self

    @field_validator("scope_filter")
    @classmethod
    def _validate_scope_filter(cls, value: str) -> str:
        """Fail at startup, not on the first query, if the scope filter is unusable."""
        value = value.strip()
        if not value:
            return ""
        try:
            parsed = json.loads(value)
            validate_filter(parsed, name="BAMCP_SCOPE_FILTER")
        except json.JSONDecodeError as exc:
            raise ValueError(f"BAMCP_SCOPE_FILTER is not valid JSON: {exc.msg}") from exc
        except FilterError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @property
    def scope_filter_dict(self) -> dict[str, Any] | None:
        """The parsed server-side scope filter, or ``None`` when none is configured."""
        return json.loads(self.scope_filter) if self.scope_filter else None

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        value = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if value not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return value

    @field_validator("log_format")
    @classmethod
    def _normalize_log_format(cls, value: str) -> str:
        value = value.lower()
        if value not in {"json", "console"}:
            raise ValueError("log_format must be 'json' or 'console'")
        return value

    @property
    def api_key_set(self) -> frozenset[str]:
        """Parsed, de-duplicated set of accepted API keys (empty => auth disabled)."""
        return frozenset(k.strip() for k in self.api_keys.split(",") if k.strip())

    @property
    def auth_enabled(self) -> bool:
        return len(self.api_key_set) > 0

    def effective_max_results(self, requested: int | None) -> int:
        """Clamp a client-requested result count to configured bounds."""
        if requested is None:
            return self.default_max_results
        return max(1, min(requested, self.max_results_limit))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide singleton Settings instance."""
    return Settings()
