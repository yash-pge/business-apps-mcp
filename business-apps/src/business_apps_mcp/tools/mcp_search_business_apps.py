"""MCP tool: ``search_business_apps``.

Retrieves the most relevant chunks from the Business Apps Knowledge Base. The
tool is retrieve-only: the calling model writes the final answer.

The module follows the ecm-mcp-server ``mcp_search_gdl.py`` layout:

* :class:`SearchBusinessAppsInput` is the single source of truth for the input
  constraints (types, required fields, bounds, filter grammar).
* The tool itself takes **flat** parameters. A single nested model argument
  would make FastMCP emit the constraints behind ``$ref``/``$defs``, which many
  MCP clients never resolve, so they would not see the bounds and would send
  out-of-range values. Flat parameters put ``minLength``/``maxLength``/
  ``minimum``/``maximum`` directly on each top-level property. The values are
  then re-validated through the model before use.
* The handler is a thin boundary with distinct blocks for input, authentication,
  the call, and result assembly, so each failure produces a distinguishable,
  summarized ``ToolError``. Full detail is logged with the trace id.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError, field_validator

from ..config import Settings
from ..errors import describe_validation_error
from ..filters import FilterError, validate_filter
from ..logging_config import get_logger
from ..models import SearchResponse
from ..retriever import (
    RetrievalAuthError,
    RetrievalCallError,
    RetrievalError,
    RetrievalInputError,
    RetrievalResultError,
    get_retriever,
)
from ..tracing import tool_call
from . import add_tool_descriptor

logger = get_logger(__name__)

TOOL_NAME = "search_business_apps"
TOOL_DESCRIPTION = (
    "Retrieve the most relevant document chunks from the Business Apps "
    "knowledge base for a natural-language query. Returns text chunks with "
    "relevance scores, the source document name (and a link when the source is "
    "a web or SharePoint page) and document metadata. Retrieve-only: it does not "
    "generate an answer — the calling model should synthesize one from the "
    "returned chunks. Use max_results to widen or narrow the set (1-25)."
)

# Bounds shown in the tool schema. The server additionally clamps max_results to
# BAMCP_MAX_RESULTS_LIMIT, so an operator can only tighten these, never loosen.
MAX_QUERY_LENGTH = 2000
MAX_RESULTS = 25

_QUERY_DESCRIPTION = (
    f"Natural-language search query (1-{MAX_QUERY_LENGTH} characters) describing the information to find."
)
_MAX_RESULTS_DESCRIPTION = (
    f"Maximum number of chunks to return (1-{MAX_RESULTS}). Start with 3-5 and raise it if recall is insufficient."
)
_FILTER_DESCRIPTION = (
    "Optional metadata filter to narrow the search, e.g. "
    '{"equals": {"key": "category", "value": "HR"}}. Combine conditions with '
    "andAll / orAll (2-5 items). It can only narrow the results, never widen them "
    "beyond what the server allows. Omit to search everything in scope."
)


class SearchBusinessAppsInput(BaseModel):
    """Validated inputs for a search_business_apps call.

    Stays the single source of truth for the field constraints; the tool's flat
    parameters mirror it and the values are re-validated through it before use.
    """

    query: str = Field(..., min_length=1, max_length=MAX_QUERY_LENGTH, description=_QUERY_DESCRIPTION)
    max_results: int = Field(5, ge=1, le=MAX_RESULTS, description=_MAX_RESULTS_DESCRIPTION)
    metadata_filter: dict[str, Any] | None = Field(None, description=_FILTER_DESCRIPTION)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("metadata_filter")
    @classmethod
    def _filter_is_valid(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            return validate_filter(value)
        except FilterError as exc:
            raise ValueError(str(exc)) from exc


def register(mcp, settings: Settings) -> None:
    """Attach the ``search_business_apps`` tool to the FastMCP app and record its descriptor."""

    # The schema default follows the operator's configured default, within bounds.
    default_max_results = max(1, min(settings.default_max_results, MAX_RESULTS))

    @mcp.tool(name=TOOL_NAME, description=TOOL_DESCRIPTION)
    def search_business_apps(
        query: Annotated[
            str,
            Field(min_length=1, max_length=MAX_QUERY_LENGTH, description=_QUERY_DESCRIPTION),
        ],
        max_results: Annotated[
            int,
            Field(ge=1, le=MAX_RESULTS, description=_MAX_RESULTS_DESCRIPTION),
        ] = default_max_results,
        metadata_filter: Annotated[
            dict[str, Any] | None,
            Field(description=_FILTER_DESCRIPTION),
        ] = None,
    ) -> SearchResponse:
        """Search the Business Apps knowledge base and return matching chunks."""
        with tool_call(TOOL_NAME, logger) as trace_id:

            def fail(message: str) -> ToolError:
                """Build the ToolError the agent sees: summarized, with the trace id."""
                return ToolError(f"{message} (trace_id={trace_id})")

            # -- Block 0: input (re-validated through the model) -------------
            try:
                params = SearchBusinessAppsInput(
                    query=query, max_results=max_results, metadata_filter=metadata_filter
                )
            except ValidationError as exc:
                detail = describe_validation_error(exc)
                logger.warning("%s: invalid input: %s", TOOL_NAME, detail)
                raise fail(f"{TOOL_NAME}: invalid input: {detail}") from exc

            # Log the query length, not the text: user questions can be sensitive.
            logger.info(
                "%s: query_length=%d, max_results=%d",
                TOOL_NAME,
                len(params.query),
                params.max_results,
            )

            try:
                return get_retriever().search(
                    query=params.query,
                    max_results=params.max_results,
                    metadata_filter=params.metadata_filter,
                )
            except RetrievalInputError as exc:
                logger.warning("%s: rejected input: %s", TOOL_NAME, exc)
                raise fail(f"{TOOL_NAME}: invalid input: {exc}") from exc
            # -- Block 1: authentication (AWS credentials / permissions) -----
            except RetrievalAuthError as exc:
                logger.error("%s: authentication failed: %s", TOOL_NAME, exc, extra={"kind": exc.kind})
                raise fail(f"{TOOL_NAME} failed during authentication: {exc}") from exc
            # -- Block 3: result assembly (malformed upstream data) ----------
            except RetrievalResultError as exc:
                logger.error("%s: result assembly failed: %s", TOOL_NAME, exc, extra={"kind": exc.kind})
                raise fail(f"{TOOL_NAME} failed assembling results: {exc}") from exc
            # -- Block 2: the call itself (timeout, connection, HTTP status) -
            except RetrievalCallError as exc:
                logger.warning("%s: call failed: %s", TOOL_NAME, exc, extra={"kind": exc.kind})
                raise fail(f"{TOOL_NAME} failed: {exc}") from exc
            except RetrievalError as exc:
                logger.warning("%s: retrieval failed: %s", TOOL_NAME, exc, extra={"kind": exc.kind})
                raise fail(f"{TOOL_NAME} failed: {exc}") from exc
            except Exception as exc:
                # A bug or an unforeseen failure: keep the details in the logs and
                # give the caller only a generic message.
                logger.exception("%s: unexpected error", TOOL_NAME)
                raise fail(f"{TOOL_NAME} failed unexpectedly") from exc

    logger.debug("Registered tool: %s", TOOL_NAME)
    add_tool_descriptor(TOOL_NAME, TOOL_DESCRIPTION)
