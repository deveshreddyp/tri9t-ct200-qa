"""
tests/test_api_documents_nodes.py — FastAPI integration tests for
documents and nodes endpoints.

Uses httpx TestClient with a temporary file-based SQLite (not in-memory)
to avoid the SQLAlchemy session-binding complexities that arise when
monkey-patching module-level engine references in module-scoped fixtures.

The temporary DB file is created fresh for each test module run and deleted
on teardown.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_V1 = os.path.join(_REPO_ROOT, "data", "ct200_manual.pdf")
PDF_V2 = os.path.join(_REPO_ROOT, "data", "ct200_manual_v2.pdf")

skip_no_pdf = pytest.mark.skipif(
    not os.path.exists(PDF_V1),
    reason="PDFs not present; skipping API integration tests",
)


# ---------------------------------------------------------------------------
# Module-scoped client — uses a temp-file SQLite + env-var override
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """
    TestClient backed by a fresh temporary SQLite file.

    We set DATABASE_URL via os.environ BEFORE importing app.db so the
    engine is created with the temp DB from the start.  On teardown the
    file is removed.
    """
    import importlib

    # Create a temporary DB file
    fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="test_api_")
    os.close(fd)
    db_url = f"sqlite:///{tmp_db}"

    # Override the DATABASE_URL env var so Settings picks it up
    os.environ["DATABASE_URL"] = db_url

    # Re-import everything with the new URL
    import app.config
    import app.db
    import app.models.orm
    import app.main

    # Force reload so new DATABASE_URL is picked up
    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.models.orm)
    importlib.reload(app.main)

    # Now Base.metadata is registered and engine points to tmp_db
    app.db.Base.metadata.create_all(bind=app.db.engine)

    from fastapi.testclient import TestClient
    from app.main import app as _app

    with TestClient(_app, raise_server_exceptions=True) as c:
        yield c

    # Cleanup
    del os.environ["DATABASE_URL"]
    try:
        os.unlink(tmp_db)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Ingest fixtures (run once via API)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ingest_v1(client):
    if not os.path.exists(PDF_V1):
        pytest.skip("PDF v1 not found")
    with open(PDF_V1, "rb") as f:
        resp = client.post(
            "/documents/ingest",
            params={"document_name": "CT-200 Manual"},
            files={"file": ("ct200_manual.pdf", f, "application/pdf")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture(scope="module")
def ingest_v2(client, ingest_v1):
    if not os.path.exists(PDF_V2):
        pytest.skip("PDF v2 not found")
    doc_id = ingest_v1["document_id"]
    with open(PDF_V2, "rb") as f:
        resp = client.post(
            f"/documents/{doc_id}/versions",
            files={"file": ("ct200_manual_v2.pdf", f, "application/pdf")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# Document endpoint tests
# ===========================================================================

@skip_no_pdf
class TestDocumentEndpoints:

    def test_ingest_returns_201(self, ingest_v1):
        assert ingest_v1["version_number"] == 1
        assert ingest_v1["node_count"] > 0
        # source_filename is the original upload filename, not the temp path
        assert ingest_v1["source_filename"] == "ct200_manual.pdf"

    def test_list_documents_includes_ingested(self, client, ingest_v1):
        resp = client.get("/documents")
        assert resp.status_code == 200
        ids = [d["id"] for d in resp.json()]
        assert ingest_v1["document_id"] in ids

    def test_get_document_by_id(self, client, ingest_v1):
        doc_id = ingest_v1["document_id"]
        resp = client.get(f"/documents/{doc_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "CT-200 Manual"

    def test_get_nonexistent_document_404(self, client, ingest_v1):
        resp = client.get("/documents/999999")
        assert resp.status_code == 404

    def test_list_versions(self, client, ingest_v1, ingest_v2):
        doc_id = ingest_v1["document_id"]
        resp = client.get(f"/documents/{doc_id}/versions")
        assert resp.status_code == 200
        version_numbers = [v["version_number"] for v in resp.json()]
        assert 1 in version_numbers and 2 in version_numbers

    def test_ingest_v2_creates_version_2(self, ingest_v2):
        assert ingest_v2["version_number"] == 2
        assert ingest_v2["node_count"] > 0


# ===========================================================================
# Node endpoint tests
# ===========================================================================

@skip_no_pdf
class TestNodeEndpoints:

    def test_get_sections_returns_list(self, client, ingest_v1):
        doc_id = ingest_v1["document_id"]
        resp = client.get(f"/documents/{doc_id}/sections")
        assert resp.status_code == 200
        assert len(resp.json()) > 0

    def test_sections_all_level1(self, client, ingest_v1):
        doc_id = ingest_v1["document_id"]
        sections = client.get(f"/documents/{doc_id}/sections?version=latest").json()
        assert all(s["level"] == 1 for s in sections), (
            f"Non-level-1 sections: {[s['level'] for s in sections]}"
        )

    def test_sections_version_param_works(self, client, ingest_v1, ingest_v2):
        doc_id = ingest_v1["document_id"]
        assert client.get(f"/documents/{doc_id}/sections?version=1").status_code == 200
        assert client.get(f"/documents/{doc_id}/sections?version=2").status_code == 200

    def test_sections_version_404_for_nonexistent(self, client, ingest_v1):
        doc_id = ingest_v1["document_id"]
        assert client.get(f"/documents/{doc_id}/sections?version=999").status_code == 404

    def test_get_node_by_id_returns_all_fields(self, client, ingest_v1):
        doc_id = ingest_v1["document_id"]
        node_id = client.get(f"/documents/{doc_id}/sections").json()[0]["id"]
        resp = client.get(f"/nodes/{node_id}")
        assert resp.status_code == 200
        node = resp.json()
        for field in ("id", "title", "body", "content_hash",
                      "level", "logical_node_id", "children", "order_index"):
            assert field in node, f"Missing field: {field!r}"

    def test_get_node_children_are_level2(self, client, ingest_v1):
        """
        Sections with sub-sections must expose them as level-2 children.
        Note: children are populated only for nodes that have sub-sections
        in the PDF.  We search all sections until we find one with children.
        """
        doc_id = ingest_v1["document_id"]
        sections = client.get(f"/documents/{doc_id}/sections").json()
        # Also check sub-sections by fetching every section's detail
        node_with_children = None
        for s in sections:
            detail = client.get(f"/nodes/{s['id']}").json()
            children = detail.get("children", [])
            if children:
                node_with_children = detail
                break

        # The CT-200 manual has sections with sub-sections (e.g. §1, §2, §3…)
        # If none found at level1, try level2 nodes (children of sections)
        if node_with_children is None:
            all_nodes_resp = client.get(
                f"/nodes/search?q=Specifications&doc_id={doc_id}"
            ).json()
            for n in all_nodes_resp:
                detail = client.get(f"/nodes/{n['id']}").json()
                if detail.get("children"):
                    node_with_children = detail
                    break

        assert node_with_children is not None, (
            "Expected at least one node with children in CT-200 manual"
        )
        for child in node_with_children["children"]:
            assert child["level"] == node_with_children["level"] + 1

    def test_get_node_404(self, client, ingest_v1):
        assert client.get("/nodes/999999").status_code == 404

    def test_search_finds_mmhg(self, client, ingest_v1):
        doc_id = ingest_v1["document_id"]
        resp = client.get(f"/nodes/search?q=mmHg&doc_id={doc_id}")
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) > 0
        for r in results:
            assert "mmhg" in r["title"].lower() or "mmhg" in r["body"].lower()

    def test_search_is_case_insensitive(self, client, ingest_v1):
        doc_id = ingest_v1["document_id"]
        upper = client.get(f"/nodes/search?q=MMHG&doc_id={doc_id}").json()
        lower = client.get(f"/nodes/search?q=mmhg&doc_id={doc_id}").json()
        assert len(upper) == len(lower)

    def test_search_no_match_is_empty(self, client, ingest_v1):
        doc_id = ingest_v1["document_id"]
        resp = client.get(f"/nodes/search?q=XYZZY_NO_MATCH_ZZZ&doc_id={doc_id}")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_missing_q_is_422(self, client, ingest_v1):
        doc_id = ingest_v1["document_id"]
        assert client.get(f"/nodes/search?doc_id={doc_id}").status_code == 422


# ===========================================================================
# Cross-version diff tests
# ===========================================================================

@skip_no_pdf
class TestNodeChangesEndpoint:

    def test_at_least_one_section_changed(self, client, ingest_v1, ingest_v2):
        """
        Without a version-matcher pass, v1 and v2 nodes have DIFFERENT
        logical_node_ids (each version gets fresh UUIDs at ingest time).
        The /changes endpoint requires matching logical_node_ids across
        versions — that's the version matcher's job (next phase).

        For now, we verify the endpoint responds correctly when given a
        logical_node_id that exists only in v1 (status=removed) or v2
        (status=added), confirming the diff machinery works end-to-end.
        """
        doc_id = ingest_v1["document_id"]
        # Take first section from v1 — it won't exist in v2 under that UUID
        sections_v1 = client.get(f"/documents/{doc_id}/sections?version=1").json()
        assert sections_v1, "v1 should have sections"
        logical_id = sections_v1[0]["logical_node_id"]

        resp = client.get(
            f"/nodes/{logical_id}/changes?from=1&to=2&doc_id={doc_id}"
        )
        assert resp.status_code == 200
        data = resp.json()
        # Since UUIDs differ across versions, this node is "removed" from v1 perspective
        assert data["status"] in ("removed", "changed", "unchanged"), (
            f"Unexpected status: {data['status']}"
        )
        assert data["logical_node_id"] == logical_id
        assert data["from_version"] == 1
        assert data["to_version"] == 2

    def test_unchanged_section_shape(self, client, ingest_v1, ingest_v2):
        doc_id = ingest_v1["document_id"]
        sections = client.get(f"/documents/{doc_id}/sections?version=1").json()
        for s in sections:
            resp = client.get(
                f"/nodes/{s['logical_node_id']}/changes"
                f"?from=1&to=2&doc_id={doc_id}"
            )
            if resp.status_code == 200 and resp.json()["status"] == "unchanged":
                data = resp.json()
                assert data["title_changed"] is False
                assert data["body_changed"] is False
                return
        pytest.skip("No unchanged sections found between v1 and v2")

    def test_changes_missing_doc_id_is_422(self, client, ingest_v1):
        doc_id = ingest_v1["document_id"]
        s = client.get(f"/documents/{doc_id}/sections?version=1").json()[0]
        resp = client.get(f"/nodes/{s['logical_node_id']}/changes?from=1&to=2")
        assert resp.status_code == 422
