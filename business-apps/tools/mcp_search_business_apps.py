"""
MCP tool: `search_business_apps`.
    "Search the Business Apps knowledge base."

Same structure as ecm-mcp-server's `mcp_search_gdl.py`: flat tool parameters
re-validated through an input model, an Entra access token fetched first (so an
auth failure is reported distinctly), then the retrieve API call, then result
assembly through pydantic models. Every failure becomes a ToolError at this
boundary.
"""

import logging
import os
from typing import Annotated, Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp.exceptions import ToolError

from .business_apps_search import search_business_apps_vectordb
from .business_apps_token import get_access_token

logger = logging.getLogger(__name__)

TOOL_NAME = "search_business_apps"
TOOL_DESCRIPTION = (
    """Search the Business Apps knowledge base: a Bedrock Knowledge Base of PG&E
    business application documentation. Returns the text chunks most relevant to a
    natural-language query, each with its relevance score, source document name (and
    a link when the source is a web or SharePoint page) and document metadata.

    Retrieve-only: it does not generate an answer. Synthesize the answer from the
    returned chunks. Call once with a focused query, and raise max_results (1-25,
    default 5) if you need a wider set of chunks.

    Returns a list of chunks ordered by relevance (highest first)."""
)

MAX_RESULTS = 25

# Location types whose URL is a user-facing page and safe to return.
_URL_LOCATIONS = {
    "WEB": ("webLocation", "url"),
    "CONFLUENCE": ("confluenceLocation", "url"),
    "SHAREPOINT": ("sharePointLocation", "url"),
    "SALESFORCE": ("salesforceLocation", "url"),
}


class SearchBusinessAppsInput(BaseModel):
    """Validated inputs for a search_business_apps call.

    Single source of truth for the field constraints. The tool is exposed with
    *flat* parameters (as in ecm's search_gdl) so FastMCP inlines each field's
    bounds into the top level of the tool schema where every client can see it.
    """

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Natural-language search query describing the information to find.",
    )
    max_results: int = Field(
        5,
        ge=1, le=MAX_RESULTS,
        description="Maximum number of chunks returned (1-25). Start with 3-5 and "
                    "raise it if recall is insufficient.",
    )


class BusinessAppsMetadata(BaseModel):
    """Document metadata exposed to clients. Undeclared fields are dropped."""
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    category: Optional[str] = None
    subcategory: Optional[str] = None
    document_name: Optional[str] = Field(default=None, alias="documentName")


class BusinessAppsResult(BaseModel):
    """One retrieved chunk."""
    text: str
    score: Optional[float] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    metadata: BusinessAppsMetadata


def _last_segment(value: str) -> Optional[str]:
    """File name from a path or URL: no bucket, folders or query string."""
    return value.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or None


def _source_from_location(location: Any) -> tuple[Optional[str], Optional[str]]:
    """Return (source_name, source_url). Raw storage paths (S3) are never returned."""
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


def _to_results(retrieval_results: list[Dict[str, Any]]) -> list[BusinessAppsResult]:
    """Validate Bedrock-shaped retrieval results into BusinessAppsResult models.

    A record of the wrong shape raises (pydantic ValidationError / TypeError /
    KeyError), which the tool boundary turns into a ToolError.
    """
    results = []
    for hit in retrieval_results:
        text = hit["content"]["text"]
        if not isinstance(text, str):
            raise TypeError("content.text must be a string")
        source_name, source_url = _source_from_location(hit.get("location"))
        results.append(
            BusinessAppsResult(
                text=text,
                score=hit.get("score"),
                source_name=source_name,
                source_url=source_url,
                metadata=BusinessAppsMetadata.model_validate(hit.get("metadata") or {}),
            )
        )
    return results


def register(mcp) -> None:
    """Attach the search_business_apps tool to the FastMCP app and record its descriptor."""

    from mcp.server.fastmcp import Context

    from . import add_tool_descriptor
    from .logging_utils import tool_call

    @mcp.tool(name=TOOL_NAME, description=TOOL_DESCRIPTION)
    def search_business_apps(
        query: Annotated[
            str,
            Field(
                min_length=1,
                max_length=2000,
                description="Natural-language search query describing the information to find.",
            ),
        ],
        ctx: Context,
        max_results: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_RESULTS,
                description="Maximum number of chunks returned (1-25). Start with 3-5 and "
                            "raise it if recall is insufficient.",
            ),
        ] = 5,
    ) -> list[BusinessAppsResult]:
        """Query the Business Apps knowledge base via the retrieve API.

            Args:
                query (str): Natural-language search query.
                ctx (Context): FastMCP-injected request context. Used to bind the
                    inbound trace id so every log line for this call correlates.
                max_results (int): Max chunks to return (1-25, default 5).
            Returns:
                List of BusinessAppsResult
        """
        with tool_call(ctx, TOOL_NAME, logger):
            try:
                params = SearchBusinessAppsInput(query=query, max_results=max_results)
            except ValueError as e:
                raise ToolError(f"{TOOL_NAME}: invalid input: {e}") from e
            return _run_search_business_apps(params)

    def _run_search_business_apps(params: SearchBusinessAppsInput) -> list[BusinessAppsResult]:
        """Handler body, separated from the boundary wrapper (as in ecm)."""
        query = params.query.strip()
        max_results = params.max_results
        if not query:
            raise ToolError(f"{TOOL_NAME}: invalid input: query must not be blank.")

        kb_id = os.getenv("KB_ID")
        rerank = os.getenv("RERANK", "false").lower() == "true"
        # Log the query length, not the raw text — avoids writing user query
        # content into logs.
        logger.info("%s: query_length=%d, n=%d", TOOL_NAME, len(query), max_results)

        # Fail fast on missing server configuration rather than sending a None
        # kb_id downstream.
        missing_config = [
            name
            for name, value in (
                ("KB_ID", kb_id),
                ("API_ENDPOINT", os.getenv("API_ENDPOINT")),
                ("RERANKING_MODEL_ARN", os.getenv("RERANKING_MODEL_ARN") if rerank else "n/a"),
            )
            if not value
        ]
        if missing_config:
            logger.error(
                "%s: missing required configuration: %s", TOOL_NAME, ", ".join(missing_config)
            )
            raise ToolError(
                f"{TOOL_NAME} is not configured correctly; missing required "
                f"environment variable(s): {', '.join(missing_config)}."
            )

        logger.info("%s: search kb=%s...", TOOL_NAME, kb_id)

        # Fetch the access token first so an auth failure is logged and reported
        # distinctly from a search failure.
        try:
            access_token = get_access_token()
        except Exception as e:
            logger.error("%s: failed to obtain access token (%s)", TOOL_NAME, type(e).__name__)
            # Entra/Azure errors can embed response details: keep them out of the
            # message unless it is our own "missing fields" error.
            detail = str(e) if isinstance(e, RuntimeError) else "Entra ID rejected the sign-in"
            raise ToolError(f"{TOOL_NAME} failed during authentication: {detail}") from e

        try:
            retrieval_results = search_business_apps_vectordb(
                kb_id=kb_id,
                access_token=access_token,
                query=query,
                number_of_results=max_results,
                rerank=rerank,
            )
        except Exception as e:
            # The search layer raises plain exceptions; this is the MCP boundary,
            # so translate any failure into a ToolError for the model.
            logger.exception("%s: unexpected error during search", TOOL_NAME)
            raise ToolError(f"{TOOL_NAME} failed: {e}") from e

        logger.info("%s: returned %d chunk(s)", TOOL_NAME, len(retrieval_results))

        try:
            results = _to_results(retrieval_results)
        except Exception as e:
            logger.exception("%s: failed to assemble results from search response", TOOL_NAME)
            raise ToolError(f"{TOOL_NAME} failed: search returned a malformed response.") from e

        return results

    logger.debug("Registered tool: %s", TOOL_NAME)
    add_tool_descriptor(TOOL_NAME, TOOL_DESCRIPTION)
