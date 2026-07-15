from sqlalchemy.orm import Session
from app.models.orm import Selection
from app.models.schemas import SelectionCreate

def create_selection(db: Session, selection_in: SelectionCreate) -> Selection:
    """
    Create a new named selection of nodes locked to specific version content hashes.
    """
    # Stub implementation
    pass

def get_selection(db: Session, selection_id: int) -> Selection:
    """
    Retrieve a selection by its ID.
    """
    # Stub implementation
    pass
