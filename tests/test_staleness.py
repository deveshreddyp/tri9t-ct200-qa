import os
import pytest
import tempfile
import importlib
from fastapi.testclient import TestClient

from app.models.orm import Node, DocumentVersion
from app.staleness.service import check_staleness

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_V1 = os.path.join(_REPO_ROOT, "data", "ct200_manual.pdf")
PDF_V2 = os.path.join(_REPO_ROOT, "data", "ct200_manual_v2.pdf")

skip_no_pdf = pytest.mark.skipif(
    not os.path.exists(PDF_V1) or not os.path.exists(PDF_V2),
    reason="PDFs not present; skipping integration tests",
)

@pytest.fixture(scope="module")
def client_and_db():
    fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="test_api_staleness_")
    os.close(fd)
    db_url = f"sqlite:///{tmp_db}"
    os.environ["DATABASE_URL"] = db_url

    import app.config
    import app.db
    import app.models.orm
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.models.orm)
    importlib.reload(app.main)

    app.db.Base.metadata.create_all(bind=app.db.engine)

    from app.main import app as _app

    with TestClient(_app, raise_server_exceptions=True) as c:
        TestingSessionLocal = app.db.SessionLocal
        yield c, TestingSessionLocal

    del os.environ["DATABASE_URL"]
    try:
        os.unlink(tmp_db)
    except OSError:
        pass


@pytest.fixture(scope="module")
def ingest_v1_and_v2(client_and_db):
    client, db_factory = client_and_db
    if not os.path.exists(PDF_V1) or not os.path.exists(PDF_V2):
        pytest.skip("PDFs not found")
        
    with open(PDF_V1, "rb") as f:
        resp = client.post(
            "/documents/ingest",
            params={"document_name": "CT-200 Staleness Test"},
            files={"file": ("ct200_manual.pdf", f, "application/pdf")},
        )
    v1_data = resp.json()
    doc_id = v1_data["document_id"]
    
    with open(PDF_V2, "rb") as f:
        resp2 = client.post(
            f"/documents/{doc_id}/versions",
            files={"file": ("ct200_manual_v2.pdf", f, "application/pdf")},
        )
    v2_data = resp2.json()
    
    return doc_id, v1_data["version_id"], v2_data["version_id"]

@skip_no_pdf
def test_generation_unchanged_not_stale(client_and_db, ingest_v1_and_v2):
    client, db_factory = client_and_db
    doc_id, v1_id, v2_id = ingest_v1_and_v2
    
    db = db_factory()
    
    # Cleaning Instructions is unchanged between v1 and v2
    search_v1 = client.get(f"/nodes/search?q=Cleaning Instructions&doc_id={doc_id}&version=1").json()
    assert len(search_v1) > 0
    node = db.query(Node).filter_by(id=search_v1[0]["id"]).first()
    
    gen_mock = {
        "generation_id": "test-unchanged",
        "source_snapshot": [
            {
                "logical_node_id": node.logical_node_id,
                "node_id": node.id,
                "content_hash": node.content_hash,
                "title": node.title
            }
        ]
    }
    
    staleness = check_staleness(db, gen_mock)
    
    assert staleness.stale is False
    assert len(staleness.reasons) == 0
    assert len(staleness.diffs) == 0
    db.close()

@skip_no_pdf
def test_generation_changed_is_stale(client_and_db, ingest_v1_and_v2):
    client, db_factory = client_and_db
    doc_id, v1_id, v2_id = ingest_v1_and_v2
    
    db = db_factory()
    
    # Battery Life changed between v1 and v2
    search_v1 = client.get(f"/nodes/search?q=Battery Life&doc_id={doc_id}&version=1").json()
    assert len(search_v1) > 0
    node = db.query(Node).filter_by(id=search_v1[0]["id"]).first()
    
    gen_mock = {
        "generation_id": "test-changed",
        "source_snapshot": [
            {
                "logical_node_id": node.logical_node_id,
                "node_id": node.id,
                "content_hash": node.content_hash,
                "title": node.title
            }
        ]
    }
    
    staleness = check_staleness(db, gen_mock)
    
    assert staleness.stale is True
    assert f"source section '{node.title}' text changed" in staleness.reasons
    assert len(staleness.diffs) == 1
    assert "300" in staleness.diffs[0]
    assert "250" in staleness.diffs[0]
    db.close()

@skip_no_pdf
def test_generation_removed_is_stale(client_and_db, ingest_v1_and_v2):
    client, db_factory = client_and_db
    doc_id, v1_id, v2_id = ingest_v1_and_v2
    
    db = db_factory()
    
    # To test a removed node, we use a real node from V1 but fake its logical_node_id
    search_v1 = client.get(f"/nodes/search?q=Battery Life&doc_id={doc_id}&version=1").json()
    v1_node_id = search_v1[0]["id"]
    
    gen_mock = {
        "generation_id": "test-removed",
        "source_snapshot": [
            {
                "logical_node_id": "deleted-logical-id-1234",
                "node_id": v1_node_id,
                "content_hash": "old_hash",
                "title": "Deleted Section"
            }
        ]
    }
    
    staleness = check_staleness(db, gen_mock)
    
    assert staleness.stale is True
    # Reason should mention removed
    assert any("removed" in reason for reason in staleness.reasons)
    db.close()
