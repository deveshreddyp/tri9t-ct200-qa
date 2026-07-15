"""
app/models/orm.py — SQLAlchemy ORM models per TRD.md section 3.

Schema summary
--------------
Document          id, name, created_at
DocumentVersion   id, document_id (FK), version_number, ingested_at, source_filename
Node              id, document_version_id (FK), logical_node_id, parent_id (FK self, nullable),
                  level, title, body, order_index, content_hash
Selection         id, name, created_at
SelectionItem     id, selection_id (FK), node_id (FK), logical_node_id,
                  content_hash_at_selection

Design notes
------------
- `Node.logical_node_id` is a stable UUID-string that persists across
  versions for "the same section".  Two Node rows (v1 and v2) representing
  the same logical section share this value but have different PKs and
  potentially different content_hash.

- `SelectionItem.content_hash_at_selection` is stored redundantly (not
  only via the FK) so staleness checks work even if a Node row were later
  deleted/migrated.  It is the authoritative audit trail.

- `Node.parent_id` is a self-referential FK to `nodes.id` (integer PK),
  not to `logical_node_id`, because within a version the integer PK is
  unique and unambiguous.

- `UniqueConstraint` on (document_id, version_number) prevents duplicate
  version numbers for the same document.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base


def _utcnow():
    """Timezone-aware UTC now (avoids the deprecated datetime.utcnow)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    versions = relationship(
        "DocumentVersion",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentVersion.version_number",
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# DocumentVersion
# ---------------------------------------------------------------------------
class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_doc_version"),
    )

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number = Column(Integer, nullable=False)
    ingested_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    source_filename = Column(String(512), nullable=False)

    document = relationship("Document", back_populates="versions")
    nodes = relationship(
        "Node",
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="Node.order_index",
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentVersion id={self.id} doc={self.document_id}"
            f" v={self.version_number}>"
        )


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------
class Node(Base):
    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True, index=True)
    document_version_id = Column(
        Integer,
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Stable cross-version identifier (UUID string assigned by parser/matcher)
    logical_node_id = Column(String(36), nullable=False, index=True)
    # Self-referential FK: integer PK of the parent Node within the same version
    parent_id = Column(Integer, ForeignKey("nodes.id"), nullable=True, index=True)
    level = Column(Integer, nullable=False)          # 0=preamble, 1=top, 2=sub, …
    title = Column(String(1024), nullable=False)
    body = Column(Text, nullable=False, default="")
    order_index = Column(Integer, nullable=False)    # physical PDF order among siblings
    content_hash = Column(String(64), nullable=False)  # sha256 hex

    version = relationship("DocumentVersion", back_populates="nodes")
    parent = relationship("Node", remote_side=[id], backref="children")

    def __repr__(self) -> str:
        return (
            f"<Node id={self.id} level={self.level}"
            f" logical={self.logical_node_id[:8]}…"
            f" title={self.title[:40]!r}>"
        )


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
class Selection(Base):
    __tablename__ = "selections"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    items = relationship(
        "SelectionItem",
        back_populates="selection",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Selection id={self.id} name={self.name!r}>"


# ---------------------------------------------------------------------------
# SelectionItem
# ---------------------------------------------------------------------------
class SelectionItem(Base):
    __tablename__ = "selection_items"

    id = Column(Integer, primary_key=True, index=True)
    selection_id = Column(
        Integer,
        ForeignKey("selections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Points at the SPECIFIC versioned Node row (the concrete pin)
    node_id = Column(
        Integer, ForeignKey("nodes.id", ondelete="RESTRICT"), nullable=False
    )
    # Redundant fields for belt-and-suspenders staleness audit trail
    logical_node_id = Column(String(36), nullable=False)
    content_hash_at_selection = Column(String(64), nullable=False)

    selection = relationship("Selection", back_populates="items")
    node = relationship("Node")

    def __repr__(self) -> str:
        return (
            f"<SelectionItem id={self.id}"
            f" selection={self.selection_id} node={self.node_id}>"
        )

