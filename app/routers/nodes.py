from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List
from app.db import get_db
from app.models.schemas import NodeResponse

router = APIRouter(tags=["nodes"])

@router.get("/documents/{doc_id}/sections", response_model=List[NodeResponse])
def get_sections(doc_id: int, version: str = "latest", db: Session = Depends(get_db)):
    """
    List top-level sections/nodes for a document, filterable by version (default: latest).
    """
    # Stub implementation
    return []

@router.get("/nodes/{node_id}", response_model=NodeResponse)
def get_node(node_id: int, db: Session = Depends(get_db)):
    """
    Get detailed information for a single node by its primary key ID, including full text.
    """
    # Stub implementation
    pass

@router.get("/nodes/search", response_model=List[NodeResponse])
def search_nodes(q: str, version: str = "latest", db: Session = Depends(get_db)):
    """
    Search nodes across the document matching heading or body text.
    """
    # Stub implementation
    return []

@router.get("/nodes/{logical_node_id}/changes")
def get_node_changes(logical_node_id: str, from_version: int = Query(..., alias="from"), to_version: int = Query(..., alias="to"), db: Session = Depends(get_db)):
    """
    Retrieve diff/changes for a given logical node ID between two version numbers.
    """
    # Stub implementation
    return {}
