"""
app/ingestion/service.py — Ingest a PDF into the relational database.

Responsibilities
----------------
1. Parse the PDF into a list of ParsedNode (via pdf_tree.parse_pdf_to_tree).
2. Upsert a Document row (find-or-create by name).
3. Create a new DocumentVersion row with auto-incremented version_number.
4. Persist all ParsedNode objects as Node rows, mapping the in-memory
   parent UUIDs → integer PKs assigned by SQLite.
5. Return an IngestResult describing what was stored.

Idempotency / safety
--------------------
- If the same (document_name, source_filename) combination is submitted
  twice, a NEW version is created — we never overwrite existing versions.
  The caller is responsible for deciding whether to re-ingest.
- The entire ingest is wrapped in a single transaction: either all nodes
  are stored or none are (rollback on any error).

Parent mapping strategy
-----------------------
ParsedNode uses UUID strings as node_id / parent_id (in-memory).
ORM Node rows use integer PKs.  We perform a two-pass insert:

  Pass 1: Insert all Node rows with parent_id=NULL (to obtain integer PKs).
  Pass 2: For each node that had a UUID parent, look up the ORM PK via
          uuid_to_pk map and update parent_id with a bulk UPDATE.

This avoids ordering dependencies and works regardless of tree depth.
"""

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.orm import Document, DocumentVersion, Node
from app.parser.pdf_tree import ParsedNode, parse_pdf_to_tree
from app.versioning.matcher import match_version_nodes


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------
@dataclass
class IngestResult:
    document_id: int
    version_id: int
    version_number: int
    node_count: int
    source_filename: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_or_create_document(db: Session, name: str) -> Document:
    """Return existing Document by name, or create a new one."""
    doc = db.query(Document).filter_by(name=name).first()
    if doc is None:
        doc = Document(name=name)
        db.add(doc)
        db.flush()  # populate doc.id without committing
    return doc


def _next_version_number(db: Session, document_id: int) -> int:
    """Return the next sequential version number for this document."""
    from sqlalchemy import func
    max_ver = (
        db.query(func.max(DocumentVersion.version_number))
        .filter_by(document_id=document_id)
        .scalar()
    )
    return 1 if max_ver is None else max_ver + 1


def _parsed_nodes_to_orm(
    parsed_nodes: List[ParsedNode],
    version_id: int,
) -> List[Node]:
    """
    Convert ParsedNode dataclasses to Node ORM objects.

    All parent_id columns are set to NULL here.  The caller resolves
    them in a second pass once integer PKs are known.
    """
    return [
        Node(
            document_version_id=version_id,
            logical_node_id=node.logical_node_id,
            parent_id=None,          # resolved in second pass
            level=node.level,
            title=node.title,
            body=node.body,
            order_index=node.order_index,
            content_hash=node.content_hash,
        )
        for node in parsed_nodes
    ]


def _resolve_parents(
    db: Session,
    orm_nodes: List[Node],
    parsed_nodes: List[ParsedNode],
) -> None:
    """
    Second-pass: set integer parent_id on each ORM Node using the
    uuid → integer PK mapping built after the first flush.

    We match by logical_node_id position (same list order as parsed_nodes).
    """
    # Build map: uuid (node_id of ParsedNode) → ORM integer PK
    uuid_to_pk: Dict[str, int] = {
        pn.node_id: orm.id
        for pn, orm in zip(parsed_nodes, orm_nodes)
    }

    for pn, orm_node in zip(parsed_nodes, orm_nodes):
        if pn.parent_id is not None:
            parent_pk = uuid_to_pk.get(pn.parent_id)
            if parent_pk is not None:
                orm_node.parent_id = parent_pk
            # If parent not found (shouldn't happen with a well-formed tree),
            # leave parent_id=NULL (becomes a root node) — documented failure mode.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_pdf(
    db: Session,
    pdf_path: str,
    document_name: Optional[str] = None,
    original_filename: Optional[str] = None,
) -> IngestResult:
    """
    Parse *pdf_path* and persist it as a new Document version.

    Parameters
    ----------
    db                SQLAlchemy session (caller owns transaction boundaries).
    pdf_path          Absolute or relative path to the PDF file on disk.
    document_name     Name under which to register/find the Document.
                      Defaults to the PDF filename stem (without extension).
    original_filename The original filename (if uploaded). Falls back to
                      os.path.basename(pdf_path) if not provided.

    Returns
    -------
    IngestResult  with IDs, version number, and node count.

    Raises
    ------
    FileNotFoundError  if pdf_path does not exist.
    ValueError         if the parser returns an empty tree.
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if original_filename:
        source_filename = original_filename
    else:
        source_filename = os.path.basename(pdf_path)

    if document_name is None:
        document_name = os.path.splitext(source_filename)[0]

    # 1. Parse
    parsed_nodes: List[ParsedNode] = parse_pdf_to_tree(pdf_path)
    if not parsed_nodes:
        raise ValueError(f"Parser returned empty tree for {pdf_path!r}")

    # 2. Document (find or create)
    doc = _get_or_create_document(db, document_name)

    # 3. DocumentVersion
    ver_num = _next_version_number(db, doc.id)
    version = DocumentVersion(
        document_id=doc.id,
        version_number=ver_num,
        source_filename=source_filename,
    )
    db.add(version)
    db.flush()  # populate version.id

    # 3. Apply version matching if there's a previous version
    prev_version = db.query(DocumentVersion).filter_by(document_id=doc.id, version_number=ver_num - 1).first()
    if prev_version:
        v1_nodes = db.query(Node).filter_by(document_version_id=prev_version.id).all()
        match_version_nodes(v1_nodes, parsed_nodes)

    # 4. Pass 1 — insert nodes with parent_id=NULL
    orm_nodes = _parsed_nodes_to_orm(parsed_nodes, version.id)
    db.add_all(orm_nodes)
    db.flush()  # populate orm_node.id for all rows

    # 5. Pass 2 — resolve parent_id using uuid → pk map
    _resolve_parents(db, orm_nodes, parsed_nodes)
    db.flush()

    # 6. Commit is the caller's responsibility
    return IngestResult(
        document_id=doc.id,
        version_id=version.id,
        version_number=ver_num,
        node_count=len(orm_nodes),
        source_filename=source_filename,
    )
