"""
app/models/schemas.py — Pydantic v2 request/response schemas.

All ORM-backed schemas use model_config = ConfigDict(from_attributes=True)
so they can be built from SQLAlchemy ORM objects directly.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, RootModel


# ===========================================================================
# Document
# ===========================================================================

class DocumentResponse(BaseModel):
    id: int
    name: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class DocumentVersionResponse(BaseModel):
    id: int
    document_id: int
    version_number: int
    ingested_at: datetime
    source_filename: str
    model_config = ConfigDict(from_attributes=True)


class IngestResponse(BaseModel):
    """Returned by POST /documents/ingest and POST /documents/{id}/versions."""
    document_id: int
    version_id: int
    version_number: int
    node_count: int
    source_filename: str


# ===========================================================================
# Node
# ===========================================================================

class NodeSummary(BaseModel):
    """Lightweight node — used in section listings and child lists."""
    id: int
    document_version_id: int
    logical_node_id: str
    parent_id: Optional[int]
    level: int
    title: str
    order_index: int
    content_hash: str
    model_config = ConfigDict(from_attributes=True)


class NodeResponse(BaseModel):
    """Full node detail including body text and direct children."""
    id: int
    document_version_id: int
    logical_node_id: str
    parent_id: Optional[int]
    level: int
    title: str
    body: str
    order_index: int
    content_hash: str
    children: List[NodeSummary] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class NodeSearchResult(BaseModel):
    """Node returned in a search result (includes body for snippet)."""
    id: int
    document_version_id: int
    logical_node_id: str
    parent_id: Optional[int]
    level: int
    title: str
    body: str
    order_index: int
    content_hash: str
    model_config = ConfigDict(from_attributes=True)


class NodeDiff(BaseModel):
    """
    Describes how a logical node changed between two document versions.
    Returned by GET /nodes/{logical_node_id}/changes.
    """
    logical_node_id: str
    status: str  # "unchanged" | "changed" | "added" | "removed"
    from_version: Optional[int] = None
    to_version: Optional[int] = None
    # Present when status == "changed"
    title_changed: bool = False
    body_changed: bool = False
    unified_diff: Optional[str] = None   # truncated difflib output


# ===========================================================================
# Selection
# ===========================================================================

class SelectionItemBase(BaseModel):
    node_id: int


class SelectionCreate(BaseModel):
    name: str
    items: List[SelectionItemBase]


class SelectionItemResponse(BaseModel):
    id: int
    node_id: int
    logical_node_id: str
    content_hash_at_selection: str
    model_config = ConfigDict(from_attributes=True)


class SelectionResponse(BaseModel):
    id: int
    name: str
    created_at: datetime
    items: List[SelectionItemResponse]
    model_config = ConfigDict(from_attributes=True)


# ===========================================================================
# Generation
# ===========================================================================

class TestCaseIdea(BaseModel):
    title: str
    steps: List[str] = Field(min_length=1)
    expected_result: str = Field(min_length=1)

    model_config = ConfigDict(from_attributes=True)

class TestCaseList(RootModel):
    root: List[TestCaseIdea] = Field(min_length=3, max_length=5)

    @field_validator("root")
    def check_length(cls, v):
        if not (3 <= len(v) <= 5):
            raise ValueError("Must provide between 3 and 5 test cases")
        return v


class StalenessInfo(BaseModel):
    stale: bool
    reasons: List[str] = Field(default_factory=list)
    diffs: List[str] = Field(default_factory=list)


class GenerationResponse(BaseModel):
    generation_id: str
    selection_id: Optional[int] = None
    created_at: Optional[str] = None
    test_cases: List[TestCaseIdea]
    validation_status: str = "ok"
    staleness: StalenessInfo
    model_config = ConfigDict(from_attributes=True)
