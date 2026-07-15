"""
app/routers/nodes.py — Node browsing, search, and diff endpoints.

Endpoints (per TRD.md section 8)
---------------------------------
GET /documents/{doc_id}/sections?version=latest
    List top-level (level=1) nodes for a document.
    version param: integer version number OR "latest" (default).

GET /nodes/{node_id}
    Full node detail: id, logical_node_id, level, title, body, content_hash,
    parent_id, order_index, plus direct children (NodeSummary list).

GET /nodes/search?q=...&doc_id=...&version=latest
    Case-insensitive substring search across title AND body.
    doc_id is optional; if omitted, searches across all documents at
    the specified version (default: latest).

GET /nodes/{logical_node_id}/changes?from=1&to=2
    Compare a logical node across two document versions.
    Returns NodeDiff with status, title_changed, body_changed,
    and a truncated unified diff of the body.

Design notes
------------
- "latest" version resolution: the DocumentVersion with the highest
  version_number for that document.
- Search uses SQLAlchemy ilike (case-insensitive LIKE) which works with
  SQLite.  For Postgres, ilike is natively supported; for production at
  scale, full-text indexing should replace this.
- The /nodes/search route MUST be registered BEFORE /nodes/{node_id}
  in the router to avoid FastAPI treating "search" as an integer node_id.
"""

import difflib
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.orm import Document, DocumentVersion, Node
from app.models.schemas import NodeDiff, NodeResponse, NodeSearchResult, NodeSummary

router = APIRouter(tags=["nodes"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _resolve_version(db: Session, doc_id: int, version: str) -> DocumentVersion:
    """
    Return the DocumentVersion for doc_id at the given version specifier.
    version = "latest" → highest version_number.
    version = "<int>" → exact version number match.
    Raises 404 if document or version not found.
    """
    doc = db.query(Document).filter_by(id=doc_id).first()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {doc_id} not found",
        )

    q = db.query(DocumentVersion).filter_by(document_id=doc_id)

    if version == "latest":
        ver = q.order_by(DocumentVersion.version_number.desc()).first()
    else:
        try:
            ver_num = int(version)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"version must be an integer or 'latest', got {version!r}",
            )
        ver = q.filter_by(version_number=ver_num).first()

    if ver is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Version {version!r} not found for document {doc_id}",
        )
    return ver


def _node_or_404(db: Session, node_id: int) -> Node:
    node = db.query(Node).filter_by(id=node_id).first()
    if node is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Node {node_id} not found",
        )
    return node


def _build_node_response(db: Session, node: Node) -> NodeResponse:
    """Build a NodeResponse including direct children (sorted by order_index)."""
    children_orm = (
        db.query(Node)
        .filter_by(parent_id=node.id)
        .order_by(Node.order_index)
        .all()
    )
    children = [NodeSummary.model_validate(c) for c in children_orm]
    return NodeResponse(
        id=node.id,
        document_version_id=node.document_version_id,
        logical_node_id=node.logical_node_id,
        parent_id=node.parent_id,
        level=node.level,
        title=node.title,
        body=node.body,
        order_index=node.order_index,
        content_hash=node.content_hash,
        children=children,
    )


def _make_unified_diff(old_body: str, new_body: str, max_lines: int = 40) -> str:
    """Return a truncated unified diff string between two body texts."""
    old_lines = old_body.splitlines(keepends=True)
    new_lines = new_body.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(old_lines, new_lines, fromfile="v_old", tofile="v_new")
    )
    if len(diff_lines) > max_lines:
        diff_lines = diff_lines[:max_lines] + [f"... ({len(diff_lines) - max_lines} more lines)\n"]
    return "".join(diff_lines)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# IMPORTANT: /nodes/search must be registered BEFORE /nodes/{node_id}
@router.get("/nodes/search", response_model=List[NodeSearchResult])
def search_nodes(
    q: str = Query(..., min_length=1, description="Search term (case-insensitive)"),
    doc_id: Optional[int] = Query(None, description="Restrict search to this document"),
    version: str = Query("latest", description="Version number or 'latest'"),
    db: Session = Depends(get_db),
):
    """
    Search nodes by heading or body text (case-insensitive substring match).

    If doc_id is supplied, search is scoped to that document's version.
    If omitted, search across ALL documents at their respective latest version.
    """
    pattern = f"%{q}%"

    if doc_id is not None:
        ver = _resolve_version(db, doc_id, version)
        nodes = (
            db.query(Node)
            .filter(
                Node.document_version_id == ver.id,
                (Node.title.ilike(pattern)) | (Node.body.ilike(pattern)),
            )
            .order_by(Node.order_index)
            .all()
        )
    else:
        # Latest version for every document
        from sqlalchemy import func
        latest_subq = (
            db.query(
                DocumentVersion.document_id,
                func.max(DocumentVersion.version_number).label("max_ver"),
            )
            .group_by(DocumentVersion.document_id)
            .subquery()
        )
        latest_version_ids = (
            db.query(DocumentVersion.id)
            .join(
                latest_subq,
                (DocumentVersion.document_id == latest_subq.c.document_id)
                & (DocumentVersion.version_number == latest_subq.c.max_ver),
            )
            .subquery()
        )
        nodes = (
            db.query(Node)
            .filter(
                Node.document_version_id.in_(latest_version_ids),
                (Node.title.ilike(pattern)) | (Node.body.ilike(pattern)),
            )
            .order_by(Node.document_version_id, Node.order_index)
            .all()
        )

    return [NodeSearchResult.model_validate(n) for n in nodes]


@router.get("/nodes/{node_id}", response_model=NodeResponse)
def get_node(node_id: int, db: Session = Depends(get_db)):
    """
    Get full detail for a single node by its integer primary key.
    Response includes: all fields, body text, content_hash, and direct children.
    """
    node = _node_or_404(db, node_id)
    return _build_node_response(db, node)


@router.get(
    "/nodes/{logical_node_id}/changes",
    response_model=NodeDiff,
)
def get_node_changes(
    logical_node_id: str,
    from_version: int = Query(..., alias="from", description="Source version number"),
    to_version: int = Query(..., alias="to", description="Target version number"),
    doc_id: int = Query(..., description="Document ID"),
    db: Session = Depends(get_db),
):
    """
    Compare a logical node across two document versions.
    Returns status: unchanged | changed | added | removed, with diff if changed.
    """
    ver_from = _resolve_version(db, doc_id, str(from_version))
    ver_to = _resolve_version(db, doc_id, str(to_version))

    node_from = (
        db.query(Node)
        .filter_by(document_version_id=ver_from.id, logical_node_id=logical_node_id)
        .first()
    )
    node_to = (
        db.query(Node)
        .filter_by(document_version_id=ver_to.id, logical_node_id=logical_node_id)
        .first()
    )

    if node_from is None and node_to is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Logical node {logical_node_id!r} not found in either version",
        )

    if node_from is None:
        return NodeDiff(
            logical_node_id=logical_node_id,
            status="added",
            from_version=from_version,
            to_version=to_version,
        )

    if node_to is None:
        return NodeDiff(
            logical_node_id=logical_node_id,
            status="removed",
            from_version=from_version,
            to_version=to_version,
        )

    title_changed = node_from.title != node_to.title
    body_changed = node_from.content_hash != node_to.content_hash

    if not title_changed and not body_changed:
        return NodeDiff(
            logical_node_id=logical_node_id,
            status="unchanged",
            from_version=from_version,
            to_version=to_version,
        )

    diff_text = _make_unified_diff(node_from.body, node_to.body) if body_changed else None

    return NodeDiff(
        logical_node_id=logical_node_id,
        status="changed",
        from_version=from_version,
        to_version=to_version,
        title_changed=title_changed,
        body_changed=body_changed,
        unified_diff=diff_text,
    )


@router.get(
    "/documents/{doc_id}/sections",
    response_model=List[NodeSummary],
)
def get_sections(
    doc_id: int,
    version: str = Query("latest", description="Version number or 'latest'"),
    db: Session = Depends(get_db),
):
    """
    List top-level sections (level=1) for a document at the given version.
    Results are ordered by their physical order_index (PDF reading order).
    """
    ver = _resolve_version(db, doc_id, version)
    nodes = (
        db.query(Node)
        .filter_by(document_version_id=ver.id, level=1)
        .order_by(Node.order_index)
        .all()
    )
    return [NodeSummary.model_validate(n) for n in nodes]
