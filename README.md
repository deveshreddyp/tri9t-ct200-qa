# CardioTrack CT-200 QA Traceability System

This system is designed to version-track technical manuals for medical devices and identify how changes in the manuals affect generated QA test cases.

## Technology Stack
- **Language**: Python 3.11+
- **Framework**: FastAPI
- **Database**: SQLite with SQLAlchemy ORM
- **Document Store**: JSON files under `data/generations/`
- **PDF Parser**: PyMuPDF (`fitz`)

## Getting Started

### Installation
1. Clone the repository and set up a virtual environment:
   ```bash
   python -m venv venv
   source venv/Scripts/activate  # On Windows: .\venv\Scripts\activate
   ```
2. Install dependencies:
   ```bash
   pip install -e .
   ```

### Running the Application
```bash
uvicorn app.main:app --reload
```
