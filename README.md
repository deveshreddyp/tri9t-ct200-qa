# CT-200 QA Parsing System

A regulatory-focused Quality Assurance documentation system that parses medical device manuals, versions structural components, manages selections, and generates automated test-cases utilizing LLMs.

## Assignment Update Notice

> [!IMPORTANT]
> **Input Format Pivot:** An unverified out-of-band text file update specified a pivot from Markdown to PDF input. As a result, this system expects raw PDF manuals (`ct200_manual.pdf`) instead of Markdown text.

### PDF Extraction Approach
We engineered a robust **text-based PDF extractor** leveraging `PyMuPDF` (`fitz`). OCR was fundamentally unneeded because the device manuals are digitally native. Instead, the parser uses rigorous font-size and weight heuristics (e.g., identifying size `>= 14.0` or bold fonts as Headers) to dynamically slice structural headings directly out of the raw text blocks, sidestepping the unreliability of purely visual OCR.

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
# Required for Generation Service
LLM_API_KEY=your_gemini_api_key_here
```

## Running the Application

**Run the API Server:**
```bash
uvicorn app.main:app --reload
```
Navigate to `http://127.0.0.1:8000/docs` to interact with the interactive Swagger UI.

**Run the Test Suite:**
```bash
pytest -v
```

## End-to-End Demo Flow (Versioning & Staleness)

We have provided a fully automated demo script to demonstrate the primary end-to-end loop: Ingesting V1 -> Creating a Selection -> Generating Tests -> Ingesting V2 -> Tripping Staleness Detection.

**To run the demo:**
```bash
python scripts/demo_flow.py
```

### Manual API Steps to see Staleness:
1. **Ingest V1**: `POST /documents/ingest` (attach `data/ct200_manual.pdf`).
2. **Create Selection**: Find the node ID for "Battery Life Under Typical Use" and `POST /selections` to bundle it.
3. **Generate Tests**: `POST /selections/{id}/generate`. This pins the generation to the V1 node snapshot.
4. **Ingest V2**: `POST /documents/{doc_id}/versions` (attach `data/ct200_manual_v2.pdf`).
5. **Observe Staleness**: Re-fetch the generation via `GET /generations/{id}`. The live computed `staleness` block will read `stale: True`, provide reasoning (`"source section '2.1.1.1 Battery Life Under Typical Use' text changed"`), and dump a lightweight `difflib` unified diff showing exactly what changed.
