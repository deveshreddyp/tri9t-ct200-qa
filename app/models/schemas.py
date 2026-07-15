from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import List, Optional

class TestCaseIdea(BaseModel):
    title: str
    steps: list[str]
    expected_result: str

# Document schemas
class DocumentBase(BaseModel):
    name: str

class DocumentCreate(DocumentBase):
    pass

class DocumentResponse(DocumentBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# DocumentVersion schemas
class DocumentVersionResponse(BaseModel):
    id: int
    document_id: int
    version_number: int
    ingested_at: datetime
    source_filename: str
    model_config = ConfigDict(from_attributes=True)

# Node schemas
class NodeResponse(BaseModel):
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

# Selection schemas
class SelectionItemBase(BaseModel):
    node_id: int

class SelectionCreate(BaseModel):
    name: str
    items: list[SelectionItemBase]

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
    items: list[SelectionItemResponse]
    model_config = ConfigDict(from_attributes=True)

# Staleness schemas
class StalenessInfo(BaseModel):
    stale: bool
    reasons: list[str]
    diffs: list[str] = []

# Generation schemas
class GenerationResponse(BaseModel):
    generation_id: str
    test_cases: list[TestCaseIdea]
    staleness: StalenessInfo
    model_config = ConfigDict(from_attributes=True)
