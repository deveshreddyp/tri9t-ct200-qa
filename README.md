# CT-200 QA Traceability System

A regulatory-focused Quality Assurance documentation backend system built in Python (FastAPI). It extracts structured hierarchical data from medical device manuals (PDFs), tracks structural versioning over time, manages user selections, and generates automated QA test-case ideas utilizing LLMs. Crucially, it tracks **generation staleness** when the underlying source documentation is updated.

## Assignment Update Notice

> [!IMPORTANT]
> **Input Format Pivot:** An unverified out-of-band text file update specified a pivot from Markdown to PDF input. As a result, this system expects raw PDF manuals (`ct200_manual.pdf`) instead of Markdown text.

### PDF Extraction Approach
We engineered a robust **text-based PDF extractor** leveraging `PyMuPDF` (`fitz`). OCR was fundamentally unneeded because the device manuals are digitally native. Instead, the parser uses rigorous font-size and weight heuristics (e.g., identifying size `>= 14.0` or bold fonts as Headers) to dynamically slice structural headings directly out of the raw text blocks, sidestepping the unreliability of purely visual OCR. Irregularities such as list numbers and table headers mimicking structural headings are mitigated through heuristic guards.

## Core Architecture & Features

- **Document & Node Ingestion**: Parses PDFs into a strict parent-child relational tree stored in a SQLite database via SQLAlchemy.
- **Versioning Strategy (Matcher)**: Ingesting newer versions of the manual (V2) maps new parsed nodes against the original V1 nodes using exact path matching and fuzzy-similarity fallback, allowing nodes to maintain the same `logical_node_id` even if titles change slightly.
- **Selections (Snapshots)**: Users can group nodes into a `Selection` that permanently pins the exact `content_hash` of the text at the time of snapshotting.
- **LLM Test Generation**: Uses Google Gemini to generate QA test cases based on the `Selection`. The system utilizes strict `Pydantic` validation. If the LLM produces malformed output, the system initiates a structured single-retry loop passing the exact `ValidationError` back into the context.
- **Staleness Detection**: A decoupled JSON Generation Store holds the generated test cases. The system dynamically checks if a test case is stale by querying the newest DB document version and diffing the `content_hash` of the generation's `source_snapshot` footprint.

## Setup & Environment

**1. Virtual Environment:**
```bash
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
```

**2. Install Dependencies:**
```bash
pip install -r requirements.txt
```

**3. Environment Variables:**
Copy `.env.example` to `.env` and fill in the required keys.
```env
# Required for Generation Service (Google Gemini)
LLM_API_KEY=your_gemini_api_key_here
```

## Running the Application

**Run the API Server:**
```bash
uvicorn app.main:app --reload
```
Navigate to `http://127.0.0.1:8000/docs` to interact with the interactive Swagger UI and explore the endpoints.

**Run the Test Suite:**
```bash
pytest -v
```

## End-to-End Demo Flow (Versioning & Staleness)

We have provided a fully automated standalone demo script to demonstrate the primary end-to-end loop: Ingesting V1 -> Creating a Selection -> Generating Tests -> Ingesting V2 -> Tripping Staleness Detection.

**To run the demo:**
*(Note: The demo temporarily mocks the LLM API so it can run immediately without requiring an API key).*
```bash
python scripts/demo_flow.py
```

### Manual API Steps to see Staleness:
1. **Ingest V1**: `POST /documents/ingest` (attach `data/ct200_manual.pdf`).
2. **Create Selection**: Find the node ID for "Battery Life Under Typical Use" and `POST /selections` to bundle it.
3. **Generate Tests**: `POST /selections/{id}/generate`. This pins the generation to the V1 node snapshot.
4. **Ingest V2**: `POST /documents/{doc_id}/versions` (attach `data/ct200_manual_v2.pdf`).
5. **Observe Staleness**: Re-fetch the generation via `GET /generations/{id}`. The live computed `staleness` block will read `stale: True`, provide reasoning (`"source section '2.1.1.1 Battery Life Under Typical Use' text changed"`), and dump a lightweight `difflib` unified diff showing exactly what changed.
