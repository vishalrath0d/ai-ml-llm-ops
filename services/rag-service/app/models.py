"""Pydantic request/response schemas for rag-service."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class IngestRequest(BaseModel):
    doc_id: Optional[str] = Field(
        default=None, description="Stable ID for the document. Auto-generated if omitted."
    )
    text: Optional[str] = Field(default=None, description="Raw text to ingest.")
    url: Optional[str] = Field(
        default=None, description="URL to fetch and extract text from, instead of `text`."
    )
    source: Optional[str] = Field(
        default=None, description="Human-readable source label, e.g. 'help-center/billing'."
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_text_or_url(self) -> "IngestRequest":
        if not self.text and not self.url:
            raise ValueError("one of `text` or `url` is required")
        if self.text and self.url:
            raise ValueError("provide only one of `text` or `url`, not both")
        return self


class IngestResponse(BaseModel):
    doc_id: str
    source: Optional[str]
    chunks_ingested: int


class QueryRequest(BaseModel):
    query: str
    k: int = Field(default=5, ge=1, le=50)
    mode: Literal["naive", "hybrid"] = Field(
        default="hybrid",
        description=(
            "'naive' always does semantic vector search. 'hybrid' first checks a "
            "curated keyword_triggers.yaml table and only falls back to semantic "
            "search when no keyword trigger matches."
        ),
    )


class RetrievedChunk(BaseModel):
    chunk_id: str
    text: str
    score: float = Field(description="Cosine similarity, higher is more relevant.")
    doc_id: str
    source: Optional[str] = None
    chunk_index: Optional[int] = None


class QueryResponse(BaseModel):
    query: str
    mode: str
    retrieval_path: Literal["keyword_trigger", "semantic"]
    matched_keyword: Optional[str] = None
    routed_doc_id: Optional[str] = None
    results: List[RetrievedChunk]
