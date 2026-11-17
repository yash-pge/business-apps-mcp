"""Typed response models for the retrieval tool.

Every response is built through these models before it is returned, which gives
two guarantees (the same approach as ecm-mcp-server's GDLResult / GDLMetadata):

* **Validation.** A malformed upstream record (wrong type, missing text) fails
  validation and is turned into a safe error instead of leaking through.
* **Only declared fields are returned.** ``extra="ignore"`` drops everything the
  Knowledge Base attaches that is not declared here: Bedrock bookkeeping keys
  (``x-amz-bedrock-kb-*``), data source ids, and the raw storage location
  (bucket and key). Clients get a document name, and a link only when the source
  itself is a user-facing URL (SharePoint, web, Confluence).
"""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ResultMetadata(BaseModel):
    """Document metadata exposed to clients.

    Only the fields declared here are returned. To expose another attribute,
    declare it explicitly (and review that it is safe to show).
    """

    model_config = ConfigDict(extra="ignore")

    category: str | None = Field(default=None, description="Document category.")
    subcategory: str | None = Field(default=None, description="Document subcategory.")
    document_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("document_name", "documentName"),
        description="Human-readable document name.",
    )


class RetrievalResult(BaseModel):
    """A single retrieved chunk from the Knowledge Base."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(description="The retrieved text chunk.")
    score: float | None = Field(default=None, description="Relevance score (higher is better).")
    source_name: str | None = Field(
        default=None,
        description="Name of the source document (file name only, no storage path).",
    )
    source_url: str | None = Field(
        default=None,
        description="User-facing link to the source, when the source is a web/SharePoint page.",
    )
    metadata: ResultMetadata = Field(
        default_factory=ResultMetadata, description="Declared document metadata."
    )


class SearchResponse(BaseModel):
    """Envelope returned by the search tool."""

    model_config = ConfigDict(extra="ignore")

    query: str = Field(description="The query that was executed.")
    result_count: int = Field(description="Number of results returned.")
    reranked: bool = Field(description="Whether reranking was applied.")
    results: list[RetrievalResult] = Field(default_factory=list)
