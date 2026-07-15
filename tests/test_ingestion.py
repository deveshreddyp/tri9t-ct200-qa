"""
tests/test_ingestion.py — Integration test: ingest the real PDF and verify
that the persisted node count matches the in-memory parse.

This test uses an in-memory SQLite database so it leaves no files on disk
and can run without any .env configuration.

What is asserted
----------------
1. IngestResult.node_count == len(parse_pdf_to_tree(PDF_PATH))
   — every node produced by the parser was persisted.
2. db.query(Node).count() == result.node_count
   — the ORM row count matches the returned count.
3. DocumentVersion.version_number == 1 for a fresh ingest.
4. All Node.parent_id references resolve to a real integer PK within the
   same DocumentVersion (no dangling FK → proves the two-pass parent
   resolution worked correctly).
5. Re-ingesting the same PDF creates version 2 without touching version 1.
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.ingestion.service import ingest_pdf
from app.models.orm import Document, DocumentVersion, Node
from app.parser.pdf_tree import parse_pdf_to_tree

# ---------------------------------------------------------------------------
# Path to the real PDF under test
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_V1 = os.path.join(_REPO_ROOT, "data", "ct200_manual.pdf")
PDF_V2 = os.path.join(_REPO_ROOT, "data", "ct200_manual_v2.pdf")


# ---------------------------------------------------------------------------
# Fixture: fresh in-memory SQLite per test
# ---------------------------------------------------------------------------
@pytest.fixture()
def db():
    """Yield a scoped in-memory SQLite session; drop all tables on teardown."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    import app.models.orm  # noqa: F401 — registers models on Base
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# Skip guard: skip if PDFs are absent (CI without data files)
# ---------------------------------------------------------------------------
skip_no_pdf = pytest.mark.skipif(
    not os.path.exists(PDF_V1),
    reason=f"Real PDF not found at {PDF_V1}; skipping integration test",
)


# ===========================================================================
# Tests
# ===========================================================================

@skip_no_pdf
class TestIngestV1:
    """Ingest ct200_manual.pdf and verify persistence."""

    def test_node_count_matches_parser(self, db):
        """
        Node count in DB must equal the number of nodes the in-memory parser
        returns.  This catches any accidental drops during the ORM insert.
        """
        parsed_nodes = parse_pdf_to_tree(PDF_V1)
        result = ingest_pdf(db, PDF_V1, document_name="CT-200 Manual")
        db.commit()

        db_count = db.query(Node).filter_by(
            document_version_id=result.version_id
        ).count()

        assert result.node_count == len(parsed_nodes), (
            f"IngestResult.node_count ({result.node_count}) != "
            f"len(parse_pdf_to_tree) ({len(parsed_nodes)})"
        )
        assert db_count == len(parsed_nodes), (
            f"DB row count ({db_count}) != in-memory node count ({len(parsed_nodes)})"
        )

    def test_version_number_is_one(self, db):
        """First ingest of a new document must create version_number=1."""
        result = ingest_pdf(db, PDF_V1, document_name="CT-200 Manual")
        db.commit()

        assert result.version_number == 1, (
            f"Expected version_number=1, got {result.version_number}"
        )
        ver = db.query(DocumentVersion).filter_by(id=result.version_id).one()
        assert ver.version_number == 1

    def test_parent_ids_resolve_correctly(self, db):
        """
        Every non-null parent_id in Node table must reference a real Node.id
        within the same DocumentVersion — proves the two-pass parent
        resolution worked without dangling FKs.
        """
        result = ingest_pdf(db, PDF_V1, document_name="CT-200 Manual")
        db.commit()

        nodes = (
            db.query(Node)
            .filter_by(document_version_id=result.version_id)
            .all()
        )
        node_ids = {n.id for n in nodes}

        dangling = [
            n for n in nodes
            if n.parent_id is not None and n.parent_id not in node_ids
        ]
        assert dangling == [], (
            f"{len(dangling)} node(s) have dangling parent_id: "
            + ", ".join(f"id={n.id} parent_id={n.parent_id}" for n in dangling[:5])
        )

    def test_document_created(self, db):
        """A Document row is created with the supplied name."""
        result = ingest_pdf(db, PDF_V1, document_name="CT-200 Manual")
        db.commit()

        doc = db.query(Document).filter_by(id=result.document_id).one()
        assert doc.name == "CT-200 Manual"


@skip_no_pdf
class TestIngestV2:
    """Re-ingest creates version 2, leaving version 1 intact."""

    def test_second_ingest_creates_version_2(self, db):
        """Two ingests of the same document name must produce versions 1 and 2."""
        r1 = ingest_pdf(db, PDF_V1, document_name="CT-200 Manual")
        db.commit()
        r2 = ingest_pdf(db, PDF_V2, document_name="CT-200 Manual")
        db.commit()

        assert r1.document_id == r2.document_id, "Both versions must share the same Document"
        assert r1.version_number == 1
        assert r2.version_number == 2

    def test_v1_nodes_unchanged_after_v2_ingest(self, db):
        """
        After ingesting v2, the v1 Node rows must still exist and be
        queryable (immutability guarantee).
        """
        r1 = ingest_pdf(db, PDF_V1, document_name="CT-200 Manual")
        db.commit()
        v1_count = db.query(Node).filter_by(document_version_id=r1.version_id).count()

        r2 = ingest_pdf(db, PDF_V2, document_name="CT-200 Manual")
        db.commit()

        v1_count_after = (
            db.query(Node).filter_by(document_version_id=r1.version_id).count()
        )
        assert v1_count_after == v1_count, (
            f"V1 node count changed after v2 ingest: {v1_count} → {v1_count_after}"
        )
