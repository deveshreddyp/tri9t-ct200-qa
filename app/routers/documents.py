"""
app/routers/documents.py — Document ingestion endpoints.

Endpoints
---------
POST /documents/ingest
    Accept a PDF as a multipart file upload + optional document_name query param.
    Creates a new Document (if name is new) + DocumentVersion 1 + all Nodes.
    Returns IngestResponse.

POST /documents/{doc_id}/versions
    Re-ingest a new version of an existing document.
    Returns IngestResponse.

GET /documents
    List all documents with their version count.

GET /documents/{doc_id}/versions
    List all versions of a document.
"""

import os
import shutil
import tempfile
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingestion.service import ingest_pdf
from app.models.orm import Document, DocumentVersion
from app.models.schemas import DocumentResponse, DocumentVersionResponse, IngestResponse

router = APIRouter(prefix="/documents", tags=["documents"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_document_or_404(db: Session, doc_id: int) -> Document:
    doc = db.query(Document).filter_by(id=doc_id).first()
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {doc_id} not found",
        )
    return doc


def _save_upload_to_tmp(file: UploadFile) -> tuple[str, str]:
    """Write an uploaded file to a temp path. Returns (tmp_path, original_filename)."""
    original_name = file.filename or "upload.pdf"
    suffix = os.path.splitext(original_name)[1] or ".pdf"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            shutil.copyfileobj(file.file, f)
    finally:
        file.file.close()
    return tmp_path, original_name


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=List[DocumentResponse])
def list_documents(db: Session = Depends(get_db)):
    """List all known documents."""
    return db.query(Document).order_by(Document.id).all()


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_document(
    document_name: str = Query(..., description="Logical name for the document"),
    file: UploadFile = File(..., description="PDF file to ingest"),
    db: Session = Depends(get_db),
):
    """
    Ingest a PDF and create a new Document + Version 1 + all Nodes.
    If a document with the same name already exists, a new version is added.
    """
    tmp_path, original_name = _save_upload_to_tmp(file)
    try:
        result = ingest_pdf(db, tmp_path, document_name=document_name,
                            original_filename=original_name)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {exc}",
        )
    finally:
        os.unlink(tmp_path)

    return IngestResponse(
        document_id=result.document_id,
        version_id=result.version_id,
        version_number=result.version_number,
        node_count=result.node_count,
        source_filename=result.source_filename,
    )


@router.post(
    "/{doc_id}/versions",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_new_version(
    doc_id: int,
    file: UploadFile = File(..., description="PDF file for the new version"),
    db: Session = Depends(get_db),
):
    """
    Ingest a PDF as a new version of an existing document.
    The document_name is taken from the existing document row.
    """
    doc = _get_document_or_404(db, doc_id)
    tmp_path, original_name = _save_upload_to_tmp(file)
    try:
        result = ingest_pdf(db, tmp_path, document_name=doc.name,
                            original_filename=original_name)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {exc}",
        )
    finally:
        os.unlink(tmp_path)

    return IngestResponse(
        document_id=result.document_id,
        version_id=result.version_id,
        version_number=result.version_number,
        node_count=result.node_count,
        source_filename=result.source_filename,
    )


@router.get("/{doc_id}", response_model=DocumentResponse)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    """Get a single document by ID."""
    return _get_document_or_404(db, doc_id)


@router.get("/{doc_id}/versions", response_model=List[DocumentVersionResponse])
def list_versions(doc_id: int, db: Session = Depends(get_db)):
    """List all versions of a document."""
    _get_document_or_404(db, doc_id)
    return (
        db.query(DocumentVersion)
        .filter_by(document_id=doc_id)
        .order_by(DocumentVersion.version_number)
        .all()
    )
