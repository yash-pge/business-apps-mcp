"""Authentication middleware: gateway API key + Cognito user identity.

Two independent layers run here, mirroring the Amplify platform split:

1. **Gateway / API-key auth** (``BAMCP_API_KEYS``) — authenticates the *caller*
   (e.g. the Portkey MCP Gateway) via the ``x-api-key`` header, and records the
   forwarded end-user identity (``x-forwarded-user``) for audit. This is the
   original behavior and is unchanged.

2. **User auth** (``BAMCP_USER_AUTH_ENABLED``) — authenticates the *end user* by
   verifying a Cognito access token in the ``Authorization: Bearer`` header,
   exactly like the backend (see ``user_auth.py``). On success the verified
   claims (including ``custom:groups``) are attached to ``request.state.claims``
   and the resolved identity overrides the forwarded-identity header.

3. **Authorization** (``BAMCP_ALLOWED_GROUPS``) — once the user is known, checks
   that they belong to at least one allowed Cognito group. This is the "does
   this user have ACCESS to use this MCP server" gate. A known-but-unauthorized
   user gets **403 Forbidden** (vs. 401 for an unknown/invalid user). Empty
   allow-list = any authenticated user may use the server.

Each layer is independently toggleable. All are disabled by default so local
development works out of the box, with a loud warning when auth is off.

This is distinct from AWS authentication — the boto3 credentials the retriever
uses to call Bedrock are the application's own identity, never the user's.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from .config import Settings
from .logging_config import get_logger
from .tracing import current_trace_id
from .user_auth import CognitoUserAuthenticator, UserAuthError

logger = get_logger(__name__)

# Paths that must remain reachable without auth (health checks, etc.).
_PUBLIC_PATHS = frozenset({"/health", "/healthz", "/livez", "/readyz"})


def _as_group_list(groups) -> list[str]:
    """Normalize a ``custom:groups`` claim into a list of group names.

    Cognito may deliver groups as a real list or as a bracketed string like
    ``"[A, B]"`` (same shape the backend handles), so accept both.
    """
    if isinstance(groups, list):
        return [str(g).strip() for g in groups if str(g).strip()]
    if isinstance(groups, str):
        import re

        content = re.sub(r"[\[\]]", "", groups).strip()
        return [g.strip() for g in content.split(",") if g.strip()]
    return []


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """Validate the gateway API key and, optionally, the end-user Cognito token."""

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        authenticator: CognitoUserAuthenticator | None = None,
    ) -> None:
        super().__init__(app)
        self._settings = settings
        self._keys = settings.api_key_set
        self._key_header = settings.api_key_header.lower()
        self._identity_header = settings.identity_header.lower()

        # Lazily share one authenticator (and its JWKS cache) across requests.
        self._authenticator = authenticator
        if settings.user_auth_enabled and self._authenticator is None:
            self._authenticator = CognitoUserAuthenticator(settings)

        if not settings.auth_enabled:
            logger.warning(
                "API key auth is DISABLED (BAMCP_API_KEYS is empty). "
                "This is acceptable for local dev only — never in production."
            )
        if not settings.user_auth_enabled:
            logger.warning(
                "User (Cognito) auth is DISABLED (BAMCP_USER_AUTH_ENABLED=false). "
                "Requests are not tied to an authenticated end user."
            )

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # Forwarded identity (from the gateway) as a baseline for audit.
        identity = request.headers.get(self._identity_header, "anonymous")

        # --- Layer 1: gateway API key -------------------------------------
        if self._settings.auth_enabled:
            provided = request.headers.get(self._key_header)
            if not provided or provided not in self._keys:
                logger.warning(
                    "Rejected request: invalid or missing API key",
                    extra={"path": request.url.path, "identity": identity},
                )
                return JSONResponse(
                    {
                        "error": "unauthorized",
                        "detail": "Invalid or missing API key.",
                        "trace_id": current_trace_id(),
                    },
                    status_code=401,
                )

        # --- Layer 2: end-user Cognito token ------------------------------
        claims: dict | None = None
        if self._settings.user_auth_enabled:
            assert self._authenticator is not None  # set in __init__ when enabled
            try:
                claims = await self._authenticator.verify(
                    request.headers.get("authorization")
                )
            except UserAuthError as exc:
                logger.warning(
                    "Rejected request: user auth failed",
                    extra={"path": request.url.path, "detail": exc.detail},
                )
                return JSONResponse(
                    {
                        "error": "unauthorized",
                        "detail": exc.detail,
                        "trace_id": current_trace_id(),
                    },
                    status_code=exc.status_code,
                )
            # Prefer the verified identity over the forwarded header.
            identity = claims.get("username") or claims.get("email") or identity

            # --- Layer 3: authorization (is this user ALLOWED to use it) --
            # 401 above means "we don't know you"; 403 here means "we know you
            # but you don't have access". Only enforced when an allow-list is
            # configured, and only meaningful once the user is authenticated.
            if self._settings.authorization_enabled:
                user_groups = _as_group_list(claims.get("custom:groups"))
                allowed = self._settings.allowed_group_set
                if not any(g in allowed for g in user_groups):
                    logger.warning(
                        "Rejected request: user not in an allowed group",
                        extra={
                            "path": request.url.path,
                            "identity": identity,
                            "user_groups": user_groups,
                        },
                    )
                    return JSONResponse(
                        {
                            "error": "forbidden",
                            "detail": "You do not have access to this MCP server.",
                            "trace_id": current_trace_id(),
                        },
                        status_code=403,
                    )

        request.state.user_identity = identity
        request.state.claims = claims or {}

        logger.debug(
            "Authorized request",
            extra={"path": request.url.path, "identity": identity},
        )
        return await call_next(request)
