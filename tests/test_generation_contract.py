import os
import pytest
import tempfile
import importlib
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_V1 = os.path.join(_REPO_ROOT, "data", "ct200_manual.pdf")

VALID_JSON = """[
  {
    "title": "Test case 1",
    "steps": ["Step 1"],
    "expected_result": "Result 1"
  },
  {
    "title": "Test case 2",
    "steps": ["Step 2"],
    "expected_result": "Result 2"
  },
  {
    "title": "Test case 3",
    "steps": ["Step 3"],
    "expected_result": "Result 3"
  }
]"""

INVALID_SHAPE_JSON = """[
  {
    "title": "Test case 1"
  },
  {
    "title": "Test case 2"
  }
]"""

MALFORMED_JSON = """Here are your test cases:
[
  {
    "title": "Test case 1",
    "steps": ["Step 1"],
    "expected_result": "Result 1"
"""

@pytest.fixture(scope="module")
def client_and_db():
    fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="test_api_gen_")
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
def selection_id(client_and_db):
    client, _ = client_and_db
    
    if not os.path.exists(PDF_V1):
        pytest.skip("PDF v1 not found")
        
    with open(PDF_V1, "rb") as f:
        client.post(
            "/documents/ingest",
            params={"document_name": "CT-200 Gen Test"},
            files={"file": ("ct200_manual.pdf", f, "application/pdf")},
        )
        
    # Create a selection
    search = client.get(f"/nodes/search?q=Battery").json()
    node_id = search[0]["id"]
    
    sel = client.post(
        "/selections", 
        json={"name": "Gen Selection", "items": [{"node_id": node_id}]}
    ).json()
    return sel["id"]


@patch("app.generation.service.generate_test_cases", new_callable=AsyncMock)
def test_valid_json_returns_ok(mock_generate, client_and_db, selection_id):
    mock_generate.return_value = VALID_JSON
    client, db_factory = client_and_db
    
    resp = client.post(f"/selections/{selection_id}/generate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["validation_status"] == "ok"
    assert len(data["test_cases"]) == 3
    
    # Check JSON Store
    from app.generation.store import get_generation
    gen = get_generation(data["generation_id"])
    assert gen["validation_status"] == "ok"

@patch("app.generation.service.generate_test_cases_retry", new_callable=AsyncMock)
@patch("app.generation.service.generate_test_cases", new_callable=AsyncMock)
def test_malformed_json_repaired_on_retry(mock_gen, mock_retry, client_and_db, selection_id):
    mock_gen.return_value = MALFORMED_JSON
    mock_retry.return_value = VALID_JSON
    client, db_factory = client_and_db
    
    resp = client.post(f"/selections/{selection_id}/generate")
    assert resp.status_code == 200
    data = resp.json()
    
    assert data["validation_status"] == "repaired"
    assert len(data["test_cases"]) == 3
    
    mock_retry.assert_called_once()
    
    # Check JSON Store
    from app.generation.store import get_generation
    gen = get_generation(data["generation_id"])
    assert gen["validation_status"] == "repaired"
    assert "Repaired after initial failure" in gen["validation_notes"]
    assert gen["llm_raw_response"] == VALID_JSON


@patch("app.generation.service.generate_test_cases_retry", new_callable=AsyncMock)
@patch("app.generation.service.generate_test_cases", new_callable=AsyncMock)
def test_invalid_shape_fails_permanently_returns_422(mock_gen, mock_retry, client_and_db, selection_id):
    # Both initial and retry return invalid shape (only 2 items instead of 3-5, and missing fields)
    mock_gen.return_value = INVALID_SHAPE_JSON
    mock_retry.return_value = INVALID_SHAPE_JSON
    client, db_factory = client_and_db
    
    resp = client.post(f"/selections/{selection_id}/generate")
    
    # Per contract, we return 422 if it ultimately fails
    assert resp.status_code == 422
    
    # We must manually find the generation record since it didn't return an ID
    from app.generation.store import get_generations_by_selection
    gens = get_generations_by_selection(selection_id)
    assert len(gens) > 0
    gen = gens[0]  # The most recent is first
    assert gen["validation_status"] == "failed"
    assert len(gen["test_cases"]) == 0
    assert gen["llm_raw_response"] == INVALID_SHAPE_JSON
    assert "Retry failed" in gen["validation_notes"]
