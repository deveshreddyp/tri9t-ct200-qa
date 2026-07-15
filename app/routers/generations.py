from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
import json
from app.db import get_db
from app.models.orm import Selection, Generation, Node
from app.models.schemas import GenerationResponse, StalenessInfo
from app.generation.service import generate_and_store_test_cases

router = APIRouter(tags=["generations"])

@router.post("/selections/{selection_id}/generate", response_model=GenerationResponse)
async def generate_test_cases(selection_id: int, db: Session = Depends(get_db)):
    """
    Run the LLM to generate test-case ideas from a given selection.
    """
    selection = db.query(Selection).filter_by(id=selection_id).first()
    if not selection:
        raise HTTPException(status_code=404, detail="Selection not found")
        
    nodes = [item.node for item in selection.items]
    if not nodes:
        raise HTTPException(status_code=400, detail="Selection has no nodes")
        
    gen = await generate_and_store_test_cases(db, nodes, selection_id=selection.id)
    
    # Construct response
    test_cases = []
    if gen.test_cases_json:
        test_cases = json.loads(gen.test_cases_json)
        
    return GenerationResponse(
        generation_id=gen.id,
        selection_id=gen.selection_id,
        created_at=gen.created_at.isoformat() if gen.created_at else None,
        test_cases=test_cases,
        validation_status=gen.validation_status,
        staleness=StalenessInfo(stale=False) # Newly generated cannot be stale
    )

@router.get("/generations/by-selection/{selection_id}", response_model=List[GenerationResponse])
def get_generations_by_selection(selection_id: int, db: Session = Depends(get_db)):
    """
    Retrieve all test case generations associated with a specific selection ID.
    """
    # Stub implementation
    return []

@router.get("/generations/by-node/{logical_node_id}", response_model=List[GenerationResponse])
def get_generations_by_node(logical_node_id: str, db: Session = Depends(get_db)):
    """
    Retrieve all test case generations associated with a specific logical node.
    """
    # Stub implementation
    return []

@router.get("/generations/{generation_id}", response_model=GenerationResponse)
def get_generation(generation_id: str, db: Session = Depends(get_db)):
    """
    Fetch a single generation record by its ID, complete with current staleness status.
    """
    # Stub implementation
    pass
