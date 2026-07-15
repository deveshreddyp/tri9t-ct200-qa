from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_db
from app.models.schemas import SelectionCreate, SelectionResponse
from fastapi import HTTPException, status
import app.selections.service as svc

router = APIRouter(prefix="/selections", tags=["selections"])

@router.post("", response_model=SelectionResponse, status_code=status.HTTP_201_CREATED)
def create_selection(selection: SelectionCreate, db: Session = Depends(get_db)):
    """
    Create a named node selection (snapshot of specific versioned nodes).
    """
    try:
        new_sel = svc.create_selection(db, selection)
        db.commit()
        return new_sel
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.get("/{selection_id}", response_model=SelectionResponse)
def get_selection(selection_id: int, db: Session = Depends(get_db)):
    """
    Fetch a selection snapshot details.
    """
    sel = svc.get_selection(db, selection_id)
    if not sel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selection not found")
    return sel
