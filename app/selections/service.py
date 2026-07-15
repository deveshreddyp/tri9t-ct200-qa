from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import NoResultFound

from app.models.orm import Node, Selection, SelectionItem
from app.models.schemas import SelectionCreate

def create_selection(db: Session, request: SelectionCreate) -> Selection:
    """
    Create a new Selection with a specific list of nodes.
    Looks up each node_id to extract logical_node_id and content_hash,
    pinning the selection to the exact textual state of that version.
    """
    new_selection = Selection(name=request.name)
    db.add(new_selection)
    
    # We must flush to get the selection.id for the children, 
    # but SQLAlchemy handles relationships if we just append to the list.
    
    for item_req in request.items:
        node = db.query(Node).filter_by(id=item_req.node_id).first()
        if not node:
            raise ValueError(f"Node with id {item_req.node_id} not found.")
            
        selection_item = SelectionItem(
            node_id=node.id,
            logical_node_id=node.logical_node_id,
            content_hash_at_selection=node.content_hash,
        )
        new_selection.items.append(selection_item)
        
    db.flush()
    return new_selection

def get_selection(db: Session, selection_id: int) -> Optional[Selection]:
    """
    Retrieve a Selection by ID. Returns None if not found.
    """
    return db.query(Selection).filter_by(id=selection_id).first()
