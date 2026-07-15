from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.schemas import SelectionCreate, SelectionResponse

router = APIRouter(prefix="/selections", tags=["selections"])

@router.post("", response_model=SelectionResponse)
def create_selection(selection: SelectionCreate, db: Session = Depends(get_db)):
    """
    Create a named node selection (snapshot of specific versioned nodes).
    """
    # Stub implementation
    pass

@router.get("/{selection_id}", response_model=SelectionResponse)
def get_selection(selection_id: int, db: Session = Depends(get_db)):
    """
    Fetch a selection snapshot details.
    """
    # Stub implementation
    pass
