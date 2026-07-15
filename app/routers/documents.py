from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.schemas import DocumentResponse, DocumentVersionResponse

router = APIRouter(prefix="/documents", tags=["documents"])

@router.post("/ingest", response_model=DocumentResponse)
def ingest_document(name: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Ingest a PDF file and create version 1 of a new logical document.
    """
    # Stub implementation
    pass

@router.post("/{doc_id}/versions", response_model=DocumentVersionResponse)
def ingest_new_version(doc_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Ingest a PDF file as a new version of an existing logical document.
    """
    # Stub implementation
    pass
