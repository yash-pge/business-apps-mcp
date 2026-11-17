"""Bedrock Knowledge Base retrieval.

This module is the only place that talks to AWS. It wraps the
``bedrock-agent-runtime:Retrieve`` API against the Business Apps Knowledge Base
and turns the raw response into typed :class:`~business_apps_mcp.models.RetrievalResult`
objects.

Error layering (the same idea as ``ecm_search.py`` in ecm-mcp-server)
---------------------------------------------------------------------
Each way the call can fail is handled on its own and raised as a distinct,
*summarized* exception. The message never contains the raw AWS error body, a
stack trace, ids, or other backend internals; that detail is written to the log
(where the request's trace id is stamped on it by the logging filter).

====================  ==============================================  ===========
Failure               Raised as                                       ``kind``
====================  ==============================================  ===========
bad query / filter    :class:`RetrievalInputError`                    invalid_input
no/expired creds      :class:`RetrievalAuthError`                     auth
access denied (IAM)   :class:`RetrievalAuthError`                     auth
timeout               :class:`RetrievalCallError`                     timeout
connection failure    :class:`RetrievalCallError`                     connection
throttled (429)       :class:`RetrievalCallError`                     throttled
not found (404)       :class:`RetrievalCallError`                     not_found
rejected (400)        :class:`RetrievalCallError`                     bad_request
service error (5xx)   :class:`RetrievalCallError`                     server_error
malformed response    :class:`RetrievalResultError`                   malformed
====================  ==============================================  ===========

The tool layer maps these three stages (auth, call, result) onto separate,
distinguishable ``ToolError`` messages.
"""

from __future__ import annotations

import threading
from typing import Any

import boto3
from botocore import exceptions as boto_exc
from botocore.config import Config as BotoConfig
from botocore.parsers import ResponseParserError

from .config import Settings, get_settings
from .filters import FilterError, combine_filters
from .logging_config import get_logger
from .models import ResultMetadata, RetrievalResult, SearchResponse

logger = get_logger(__name__)


# -- exceptions ------------------------------------------------------------
class RetrievalError(RuntimeError):
    """Base class. The message is summarized and safe to show to a client."""

    stage = "call"
    kind = "error"

    def __init__(self, message: str, *, kind: str | None = None) -> None:
        super().__init__(message)
        if kind:
            self.kind = kind


class RetrievalInputError(RetrievalError):
    """The query or filter is unusable. Nothing was sent to AWS."""

    stage = "input"
    kind = "invalid_input"


class RetrievalAuthError(RetrievalError):
    """The application could not authenticate to AWS (credentials or permissions)."""

    stage = "auth"
    kind = "auth"


class RetrievalCallError(RetrievalError):
    """The call to the Knowledge Base failed (timeout, connection, HTTP status)."""

    stage = "call"


class RetrievalResultError(RetrievalError):
    """The Knowledge Base answered, but the response could not be assembled."""

    stage = "result"
    kind = "malformed"


# -- failure classification ------------------------------------------------
def _boto_exceptions(*names: str) -> tuple[type[BaseException], ...]:
    """Collect botocore exception classes by name, skipping ones this version lacks."""
    return tuple(getattr(boto_exc, name) for name in names if hasattr(boto_exc, name))


_CREDENTIAL_ERRORS = _boto_exceptions(
    "NoCredentialsError",
    "PartialCredentialsError",
    "CredentialRetrievalError",
    "ProfileNotFound",
    "InvalidConfigError",
    "NoRegionError",
    "TokenRetrievalError",
    "SSOTokenLoadError",
    "UnauthorizedSSOTokenError",
)
# ConnectTimeoutError subclasses ConnectionError, so timeouts are matched first.
_TIMEOUT_ERRORS = _boto_exceptions("ReadTimeoutError", "ConnectTimeoutError")
_CONNECTION_ERRORS = _boto_exceptions(
    "EndpointConnectionError",
    "ConnectionClosedError",
    "ProxyConnectionError",
    "SSLError",
    "ConnectionError",
)

_AUTH_CODES = frozenset(
    {
        "AccessDeniedException",
        "UnrecognizedClientException",
        "ExpiredTokenException",
        "ExpiredToken",
        "InvalidSignatureException",
        "InvalidClientTokenId",
        "SignatureDoesNotMatch",
        "AuthFailure",
        "UnauthorizedOperation",
        "MissingAuthenticationToken",
        "IncompleteSignature",
        "RequestExpired",
    }
)
_THROTTLE_CODES = frozenset(
    {"ThrottlingException", "Throttling", "ThrottledException", "TooManyRequestsException",
     "RequestLimitExceeded"}
)


def _from_client_error(exc: boto_exc.ClientError) -> RetrievalError:
    """Translate an AWS error response into a summarized exception.

    Full detail (code, HTTP status, AWS message, AWS request id) is logged; only a
    short description goes into the returned exception.
    """
    error = exc.response.get("Error", {}) or {}
    meta = exc.response.get("ResponseMetadata", {}) or {}
    code = error.get("Code", "Unknown")
    status = meta.get("HTTPStatusCode")

    logger.error(
        "Bedrock retrieve failed",
        extra={
            "error_code": code,
            "http_status": status,
            "aws_request_id": meta.get("RequestId"),
            "aws_message": error.get("Message"),
        },
    )

    if code in _AUTH_CODES or status in (401, 403):
        return RetrievalAuthError(f"AWS rejected the request: access denied or invalid credentials ({code})")
    if code in _THROTTLE_CODES or status == 429:
        return RetrievalCallError("the Knowledge Base is busy; retry shortly", kind="throttled")
    if code == "ResourceNotFoundException" or status == 404:
        return RetrievalCallError("the Knowledge Base was not found", kind="not_found")
    if code == "ValidationException" or status == 400:
        return RetrievalCallError(f"the request was rejected as invalid ({code})", kind="bad_request")
    if isinstance(status, int) and status >= 500:
        return RetrievalCallError(
            f"the Knowledge Base service returned an error (HTTP {status})", kind="server_error"
        )
    return RetrievalCallError(f"search failed ({code}, HTTP {status})")


# -- source location -------------------------------------------------------
_URL_LOCATIONS = {
    "WEB": ("webLocation", "url"),
    "CONFLUENCE": ("confluenceLocation", "url"),
    "SHAREPOINT": ("sharePointLocation", "url"),
    "SALESFORCE": ("salesforceLocation", "url"),
}


def _last_segment(value: str) -> str | None:
    """File name from a path or URL: no bucket, folders or query string."""
    return value.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or None


def _source_from_location(location: Any) -> tuple[str | None, str | None]:
    """Return ``(source_name, source_url)`` for a Knowledge Base location.

    The raw storage location (S3 bucket and key) is internal and is never
    returned: for S3 sources only the file name is kept. A URL is returned only
    when the source itself is a user-facing page (SharePoint, web, Confluence).
    Unknown location types yield nothing rather than guessing.
    """
    if not isinstance(location, dict):
        return None, None
    loc_type = str(location.get("type") or "").upper()

    if loc_type in _URL_LOCATIONS:
        key, field = _URL_LOCATIONS[loc_type]
        url = (location.get(key) or {}).get(field)
        if isinstance(url, str) and url:
            return _last_segment(url), url
        return None, None

    if loc_type == "S3":
        uri = (location.get("s3Location") or {}).get("uri")
        if isinstance(uri, str) and uri:
            return _last_segment(uri), None

    return None, None


# -- retriever -------------------------------------------------------------
class KnowledgeBaseRetriever:
    """Thin, typed wrapper around Bedrock Agent Runtime ``Retrieve``."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any | None = None
        self._lock = threading.Lock()

    # -- client lifecycle --------------------------------------------------
    @property
    def client(self) -> Any:
        """Lazily create and cache the bedrock-agent-runtime client."""
        if self._client is None:
            with self._lock:
                if self._client is None:
                    timeout = self._settings.request_timeout
                    boto_cfg = BotoConfig(
                        region_name=self._settings.aws_region,
                        retries={"max_attempts": 3, "mode": "standard"},
                        connect_timeout=min(5, timeout),
                        read_timeout=timeout,
                        user_agent_extra="business-apps-mcp/0.1.0",
                    )
                    self._client = boto3.client("bedrock-agent-runtime", config=boto_cfg)
                    logger.info(
                        "Initialized bedrock-agent-runtime client",
                        extra={
                            "region": self._settings.aws_region,
                            "kb_id": self._settings.kb_id,
                            "request_timeout": timeout,
                        },
                    )
        return self._client

    # -- public API --------------------------------------------------------
    def search(
        self,
        query: str,
        max_results: int | None = None,
        metadata_filter: dict[str, Any] | None = None,
    ) -> SearchResponse:
        """Retrieve the most relevant chunks for ``query`` from the KB.

        Raises one of the :class:`RetrievalError` subclasses on failure; see the
        module docstring for the full table.

        Parameters
        ----------
        query:
            Natural-language search query. Must be non-empty.
        max_results:
            Desired number of chunks; clamped to configured bounds.
        metadata_filter:
            Optional Bedrock retrieval filter from the caller. It is validated and
            ANDed with the server-side scope filter, so it can only narrow results.
        """
        query, request = self._build_request(query, max_results, metadata_filter)
        client = self._get_client()
        raw = self._call(client, request)
        results = self._assemble(raw)

        logger.info(
            "KB retrieve succeeded",
            extra={"result_count": len(results), "reranked": self._settings.reranking_enabled},
        )
        return SearchResponse(
            query=query,
            result_count=len(results),
            reranked=self._settings.reranking_enabled,
            results=results,
        )

    # -- stage 0: build the request ---------------------------------------
    def _build_request(
        self,
        query: str,
        max_results: int | None,
        metadata_filter: dict[str, Any] | None,
    ) -> tuple[str, dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            raise RetrievalInputError("query must be a non-empty string")

        n = self._settings.effective_max_results(max_results)
        vector_cfg: dict[str, Any] = {"numberOfResults": n}

        try:
            # Validates the caller's filter and ANDs it with the server-side scope.
            effective_filter = combine_filters(self._settings.scope_filter_dict, metadata_filter)
        except FilterError as exc:
            raise RetrievalInputError(str(exc)) from exc
        if effective_filter:
            vector_cfg["filter"] = effective_filter

        if self._settings.reranking_enabled:
            vector_cfg["rerankingConfiguration"] = self._build_reranking_config(n)

        request: dict[str, Any] = {
            "knowledgeBaseId": self._settings.kb_id,
            "retrievalQuery": {"text": query},
            "retrievalConfiguration": {"vectorSearchConfiguration": vector_cfg},
        }
        logger.debug("Submitting KB retrieve", extra={"kb_id": self._settings.kb_id, "n": n})
        return query, request

    # -- stage 1: authenticate (get a client with credentials) ------------
    def _get_client(self) -> Any:
        try:
            return self.client
        except _CREDENTIAL_ERRORS as exc:
            logger.error("AWS credentials unavailable", extra={"error": str(exc)})
            raise RetrievalAuthError("AWS credentials are missing, expired or invalid") from exc
        except boto_exc.BotoCoreError as exc:
            logger.error("AWS client could not be initialized", extra={"error": str(exc)})
            raise RetrievalAuthError("the AWS client could not be initialized") from exc

    # -- stage 2: call the Knowledge Base ---------------------------------
    def _call(self, client: Any, request: dict[str, Any]) -> Any:
        timeout = self._settings.request_timeout
        try:
            return client.retrieve(**request)
        except boto_exc.ClientError as exc:
            raise _from_client_error(exc) from exc
        except _CREDENTIAL_ERRORS as exc:
            # Credentials are resolved lazily, so they can also fail at signing time.
            logger.error("AWS credentials unavailable at call time", extra={"error": str(exc)})
            raise RetrievalAuthError("AWS credentials are missing, expired or invalid") from exc
        except _TIMEOUT_ERRORS as exc:
            logger.warning("Bedrock retrieve timed out", extra={"request_timeout": timeout, "error": str(exc)})
            raise RetrievalCallError(f"search timed out after {timeout}s", kind="timeout") from exc
        except _CONNECTION_ERRORS as exc:
            logger.error("Bedrock retrieve connection failed", extra={"error": str(exc)})
            raise RetrievalCallError("could not reach the Knowledge Base service", kind="connection") from exc
        except ResponseParserError as exc:
            logger.error("Bedrock retrieve returned an unparseable response", extra={"error": str(exc)})
            raise RetrievalResultError("search returned a malformed response") from exc
        except boto_exc.BotoCoreError as exc:
            logger.exception("Bedrock retrieve failed with an unclassified client error")
            raise RetrievalCallError("search failed (client error)", kind="client") from exc

    # -- stage 3: assemble typed results ----------------------------------
    def _assemble(self, raw: Any) -> list[RetrievalResult]:
        try:
            if not isinstance(raw, dict):
                raise TypeError("response is not an object")
            hits = raw.get("retrievalResults", [])
            if not isinstance(hits, list):
                raise TypeError("retrievalResults is not a list")
            return [self._parse_hit(hit) for hit in hits]
        except (TypeError, AttributeError, KeyError, ValueError) as exc:
            # ValueError covers pydantic's ValidationError (model validation).
            logger.exception("Could not assemble Knowledge Base results")
            raise RetrievalResultError("search returned a malformed response") from exc

    # -- internals ---------------------------------------------------------
    def _build_reranking_config(self, n: int) -> dict[str, Any]:
        """Build a VectorSearchRerankingConfiguration for the Retrieve API.

        Shape matches ``vectorSearchConfiguration.rerankingConfiguration``:
        a ``type`` discriminator plus the matching nested model config.
        """
        model_arn = (
            f"arn:aws:bedrock:{self._settings.aws_region}::foundation-model/"
            f"{self._settings.reranking_model_id}"
        )
        return {
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": model_arn},
                "numberOfRerankedResults": n,
            },
        }

    @staticmethod
    def _parse_hit(hit: dict[str, Any]) -> RetrievalResult:
        """Validate one ``retrievalResults`` entry into a RetrievalResult.

        Anything not declared on the models (Bedrock bookkeeping keys, data source
        ids, the raw storage location) is dropped. A record of the wrong shape
        raises, which :meth:`_assemble` turns into a ``RetrievalResultError``.
        """
        text = hit["content"]["text"]
        if not isinstance(text, str):
            raise TypeError("content.text must be a string")

        source_name, source_url = _source_from_location(hit.get("location"))
        return RetrievalResult(
            text=text,
            score=hit.get("score"),
            source_name=source_name,
            source_url=source_url,
            metadata=ResultMetadata.model_validate(hit.get("metadata") or {}),
        )


# Module-level singleton for the tools to import.
_retriever: KnowledgeBaseRetriever | None = None


def get_retriever() -> KnowledgeBaseRetriever:
    """Return the process-wide retriever singleton."""
    global _retriever
    if _retriever is None:
        _retriever = KnowledgeBaseRetriever()
    return _retriever
