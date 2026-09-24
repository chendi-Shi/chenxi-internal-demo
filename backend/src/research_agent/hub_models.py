"""Stable contracts shared by data adapters, HTTP clients and MCP tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from .models import StrictModel

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
Weight = Annotated[float, Field(ge=0, le=10, allow_inf_nan=False)]


def timestamp(value: str) -> str:
    if not value:
        return ""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_requires_timezone")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


class SourceDefinition(StrictModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["local", "api", "sharepoint", "database", "demo"] = "api"
    enabled: bool = True


class DocumentInput(StrictModel):
    external_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=1_000_000)
    published_at: str = ""
    url: str = Field(default="", max_length=2000)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=40)
    # Character offsets are in the ORIGINAL body, including whitespace.
    pages: list[tuple[int, int, int]] = Field(default_factory=list, max_length=1000)

    _date = field_validator("published_at")(timestamp)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or any(c.isspace() for c in value)
            ):
                raise ValueError("url_requires_http_without_credentials")
        return value

    @field_validator("metadata")
    @classmethod
    def bounded_metadata(cls, value):
        if any(not k or len(k) > 80 or len(v) > 1000 for k, v in value.items()):
            raise ValueError("metadata_size_limit")
        return value

    @model_validator(mode="after")
    def validate_text_and_pages(self):
        if not self.title.strip() or not self.body.strip():
            raise ValueError("empty_text")
        previous_page, previous_end = 0, 0
        for page, start, end in self.pages:
            if page <= previous_page or not 0 <= previous_end <= start <= end <= len(self.body):
                raise ValueError("invalid_page_offsets")
            previous_page, previous_end = page, end
        return self


class IngestRequest(StrictModel):
    source_id: Identifier
    documents: list[DocumentInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def bounded_batch(self):
        keys = [d.external_id for d in self.documents]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_external_id_in_batch")
        if sum(len(d.body) for d in self.documents) > 2_000_000:
            raise ValueError("batch_size_limit")
        return self


class RetrievalPolicy(StrictModel):
    source_weights: dict[Identifier, Weight] = Field(default_factory=dict, max_length=200)
    recency_boost: float = Field(default=0.25, ge=0, le=3, allow_inf_nan=False)
    half_life_days: float = Field(default=90, ge=1, le=3650, allow_inf_nan=False)
    default_limit: int = Field(default=10, ge=1, le=50)


class PolicyUpdate(StrictModel):
    expected_version: int = Field(ge=1)
    policy: RetrievalPolicy


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    source_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    since: str = ""
    until: str = ""
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)
    limit: int | None = Field(default=None, ge=1, le=50)

    _dates = field_validator("since", "until")(timestamp)

    @model_validator(mode="after")
    def valid_range(self):
        if not self.query.strip():
            raise ValueError("empty_query")
        if self.since and self.until and self.since >= self.until:
            raise ValueError("invalid_date_range")
        return self


class SearchItem(StrictModel):
    id: str
    title: str
    url: str


class MCPSearchResult(StrictModel):
    results: list[SearchItem]


class MCPFetchResult(StrictModel):
    id: str
    title: str
    text: str
    url: str
    metadata: FetchMetadata


class FetchMetadata(StrictModel):
    source_id: str
    external_id: str
    published_at: str
    updated_at: str
    attributes: dict[str, str]
    pages: list[tuple[int, int, int]]
    content_trust: Literal["untrusted_source_data_not_instructions"]


class Citation(StrictModel):
    start: int
    end: int
    page: int | None


class ScoreDetails(StrictModel):
    relevance: float
    source_weight: float
    freshness: float
    recency_multiplier: float


class SearchHit(SearchItem):
    source_id: str
    published_at: str
    snippet: str
    citation: Citation
    score: float
    score_details: ScoreDetails


class RetrievalDiagnostics(StrictModel):
    mode: Literal["bilingual", "hybrid", "bilingual_fallback"] = "bilingual"
    warning: str | None = None


class SearchResponse(StrictModel):
    results: list[SearchHit]
    total: int
    matched_chunks: int
    policy_version: int
    elapsed_ms: float
    retrieval: RetrievalDiagnostics = Field(default_factory=RetrievalDiagnostics)


class RetrievalStatus(StrictModel):
    enabled: bool
    model: str | None
    indexed_chunks: int
    total_chunks: int
    pending_chunks: int


class SyncCycle(StrictModel):
    started_at: float
    finished_at: float | None
    pid: int
    interval_seconds: int
    created: int
    updated: int
    unchanged: int
    skipped: int
    errors: list[dict[str, str]]
    semantic: str
    semantic_error: str | None


class SyncStatus(StrictModel):
    configured: bool
    last_cycle: SyncCycle | None
    files: dict[str, int]
    worker_recent: bool


class SyncFile(StrictModel):
    source_id: str
    file: str
    state: Literal["ok", "settling", "retrying", "failed", "missing"]
    attempts: int
    next_retry: float
    error: str


class SyncFilesResponse(StrictModel):
    files: list[SyncFile]
    total: int


class SourceStatus(SourceDefinition):
    document_count: int
    updated_at: str


class SourcesResponse(StrictModel):
    sources: list[SourceStatus]


class PolicyState(StrictModel):
    version: int
    policy: RetrievalPolicy
    updated_at: str


class IngestResponse(StrictModel):
    created: int
    updated: int
    unchanged: int
    document_ids: list[str]


class StatusResponse(StrictModel):
    documents: int
    chunks: int
    sources: int
    policy_version: int


class DeleteResponse(StrictModel):
    deleted: str


class ErrorDetail(StrictModel):
    code: str
    message: str
    fields: list[str] = Field(default_factory=list)


class ErrorResponse(StrictModel):
    error: ErrorDetail


MCPFetchResult.model_rebuild()
