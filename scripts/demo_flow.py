import os
import tempfile
import importlib
import json
from pprint import pprint

from fastapi.testclient import TestClient

def main():
    print("==================================================")
    print("      CT-200 QA System - End-to-End Demo Flow     ")
    print("==================================================\n")
    
    # 1. Setup temporary database for the demo
    fd, tmp_db = tempfile.mkstemp(suffix=".db", prefix="demo_flow_")
    os.close(fd)
    db_url = f"sqlite:///{tmp_db}"
    os.environ["DATABASE_URL"] = db_url
    os.environ["LLM_API_KEY"] = "mock"

    _REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    import sys
    sys.path.insert(0, _REPO_ROOT)

    import app.config
    import app.db
    import app.models.orm
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.models.orm)
    importlib.reload(app.main)

    app.db.Base.metadata.create_all(bind=app.db.engine)
    
    PDF_V1 = os.path.join(_REPO_ROOT, "data", "ct200_manual.pdf")
    PDF_V2 = os.path.join(_REPO_ROOT, "data", "ct200_manual_v2.pdf")

    if not os.path.exists(PDF_V1) or not os.path.exists(PDF_V2):
        print("ERROR: ct200_manual.pdf or ct200_manual_v2.pdf not found in data/ directory.")
        return

    # Mock the LLM call so the demo runs instantly without an API key
    from unittest.mock import patch, AsyncMock
    mock_json = '''
    [
      {
        "title": "Battery Life Test 1",
        "steps": ["Turn on device"],
        "expected_result": "Device starts"
      },
      {
        "title": "Battery Life Test 2",
        "steps": ["Leave running for 300 cycles"],
        "expected_result": "Device shows low battery icon at 15%"
      },
      {
        "title": "Error Code E6 Test",
        "steps": ["Trigger error E6 condition"],
        "expected_result": "E6 is displayed"
      }
    ]
    '''
    
    with TestClient(app.main.app, raise_server_exceptions=True) as client, \
         patch("app.generation.service.generate_test_cases", new_callable=AsyncMock) as mock_gen:
        
        mock_gen.return_value = mock_json
        
        # 2. Ingest V1
        print(">>> [1/6] Ingesting V1 Document (ct200_manual.pdf)...")
        with open(PDF_V1, "rb") as f:
            resp = client.post(
                "/documents/ingest",
                params={"document_name": "CT-200 Demo Document"},
                files={"file": ("ct200_manual.pdf", f, "application/pdf")},
            )
        v1_data = resp.json()
        doc_id = v1_data["document_id"]
        print(f"    Document created with ID: {doc_id} (Version 1)")
        print()

        # 3. Find 2 real sections (Battery Life, Error Codes)
        print(">>> [2/6] Searching for sections to include in the selection...")
        search_battery = client.get(f"/nodes/search?q=Battery Life&doc_id={doc_id}&version=1").json()
        search_errors = client.get(f"/nodes/search?q=Error Codes&doc_id={doc_id}&version=1").json()
        
        node_ids = []
        if search_battery:
            node_ids.append(search_battery[0]["id"])
            print(f"    Found: {search_battery[0]['title']}")
        if search_errors:
            node_ids.append(search_errors[0]["id"])
            print(f"    Found: {search_errors[0]['title']}")
            
        print()

        # 4. Create a selection
        print(">>> [3/6] Creating a Selection across these sections...")
        sel_payload = {
            "name": "Power and Errors Scope",
            "items": [{"node_id": nid} for nid in node_ids]
        }
        sel_resp = client.post("/selections", json=sel_payload).json()
        sel_id = sel_resp["id"]
        print(f"    Selection created with ID: {sel_id}")
        print()

        # 5. Generate test cases
        print(">>> [4/6] Generating Test Cases via LLM (This may take a moment)...")
        gen_resp = client.post(f"/selections/{sel_id}/generate").json()
        if "generation_id" not in gen_resp:
            print("GENERATION ERROR:", gen_resp)
        gen_id = gen_resp["generation_id"]
        
        print(f"    Generation successful! ID: {gen_id}")
        print(f"    Validation Status: {gen_resp['validation_status']}")
        print(f"    Test Cases Generated: {len(gen_resp['test_cases'])}")
        print(f"    Initial Staleness: {gen_resp['staleness']['stale']}")
        print()

        # 6. Ingest V2
        print(">>> [5/6] Ingesting V2 Document (ct200_manual_v2.pdf)...")
        with open(PDF_V2, "rb") as f:
            resp = client.post(
                f"/documents/{doc_id}/versions",
                files={"file": ("ct200_manual_v2.pdf", f, "application/pdf")},
            )
        v2_data = resp.json()
        print(f"    Version 2 created! Version ID: {v2_data['version_id']}")
        print()
        
        # 7. Retrieve generation and observe staleness
        print(">>> [6/6] Fetching the generation again to observe live Staleness Detection...")
        gen_fetch = client.get(f"/generations/{gen_id}").json()
        staleness = gen_fetch["staleness"]
        
        print("\n--- STALENESS RESULTS ---")
        print(f"Stale: {staleness['stale']}")
        if staleness['reasons']:
            print("Reasons:")
            for reason in staleness['reasons']:
                print(f"  - {reason}")
                
        if staleness['diffs']:
            print("\nLightweight Diffs:")
            for i, diff in enumerate(staleness['diffs'], 1):
                print(f"\n[Diff {i}]")
                print(diff.encode("ascii", "ignore").decode("ascii"))
        print("-------------------------\n")
        print("Demo Flow Complete!")

    # Cleanup
    del os.environ["DATABASE_URL"]
    try:
        os.unlink(tmp_db)
    except OSError:
        pass

if __name__ == "__main__":
    main()
