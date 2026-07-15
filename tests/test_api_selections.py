import os
import pytest
import tempfile
import importlib

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_V1 = os.path.join(_REPO_ROOT, "data", "ct200_manual.pdf")
PDF_V2 = os.path.join(_REPO_ROOT, "data", "ct200_manual_v2.pdf")

skip_no_pdf = pytest.mark.skipif(
    not os.path.exists(PDF_V1) or not os.path.exists(PDF_V2),
    reason="PDFs not present; skipping integration tests",
)


@pytest.fixture(scope="module")
def client():
    fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="test_api_selections_")
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

    from fastapi.testclient import TestClient
    from app.main import app as _app

    with TestClient(_app, raise_server_exceptions=True) as c:
        yield c

    del os.environ["DATABASE_URL"]
    try:
        os.unlink(tmp_db)
    except OSError:
        pass


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
    return resp.json()


@skip_no_pdf
def test_selection_pins_to_v1_text_after_v2_ingest(client, ingest_v1, ingest_v2):
    """
    Prove that a selection created against a v1 node will still return the
    exact v1 text even after v2 is ingested and changes that section's text.
    """
    doc_id = ingest_v1["document_id"]
    
    # 1. Grab a section from v1 that we know changes in v2.
    # We use "Battery Life" from v1 which contains "300".
    search_v1 = client.get(f"/nodes/search?q=Battery Life&doc_id={doc_id}&version=1").json()
    assert len(search_v1) > 0
    v1_node_id = search_v1[0]["id"]
    
    # 2. Verify v1 has '300'
    node_v1 = client.get(f"/nodes/{v1_node_id}").json()
    assert "300" in node_v1["body"]
    assert "250" not in node_v1["body"]

    # 3. Create a selection of this v1 node
    sel_resp = client.post(
        "/selections", 
        json={
            "name": "My V1 Selection",
            "items": [{"node_id": v1_node_id}]
        }
    )
    assert sel_resp.status_code == 201
    selection_id = sel_resp.json()["id"]

    # 4. Ingest v2 (happened via the fixture dependencies automatically).
    # Verify that V2 now has '250' for the same logical node.
    logical_id = node_v1["logical_node_id"]
    v2_search = client.get(f"/documents/{doc_id}/sections?version=2").json()
    
    # We must find the v2 battery life node. 
    search_v2 = client.get(f"/nodes/search?q=Battery Life&doc_id={doc_id}").json()
    # Filter for the one that has document_version_id matching ingest_v2
    v2_battery_nodes = [n for n in search_v2 if n["document_version_id"] == ingest_v2["version_id"]]
    assert len(v2_battery_nodes) > 0
    v2_node_id = v2_battery_nodes[0]["id"]
    
    node_v2 = client.get(f"/nodes/{v2_node_id}").json()
    assert "250" in node_v2["body"]
    
    # Prove it's the same logical node
    assert node_v2["logical_node_id"] == logical_id

    # 5. Fetch the selection again, and retrieve the node text it points to
    fetch_sel = client.get(f"/selections/{selection_id}").json()
    assert fetch_sel["name"] == "My V1 Selection"
    assert len(fetch_sel["items"]) == 1
    
    pinned_node_id = fetch_sel["items"][0]["node_id"]
    assert pinned_node_id == v1_node_id  # still points to the old integer PK!
    
    pinned_node = client.get(f"/nodes/{pinned_node_id}").json()
    assert "300" in pinned_node["body"]
    assert "250" not in pinned_node["body"]
    
    # Also verify the belt-and-suspenders fields
    assert fetch_sel["items"][0]["logical_node_id"] == logical_id
    assert fetch_sel["items"][0]["content_hash_at_selection"] == node_v1["content_hash"]
