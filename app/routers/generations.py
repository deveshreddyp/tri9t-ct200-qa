from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
import json
from app.db import get_db
from app.models.orm import Selection, Node
from app.models.schemas import GenerationResponse, StalenessInfo
from app.generation.service import generate_and_store_test_cases
from app.generation import store
from app.staleness.service import check_staleness

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
        
    gen_record = await generate_and_store_test_cases(nodes, selection_id=selection.id)
    
    return GenerationResponse(
        generation_id=gen_record["generation_id"],
        selection_id=gen_record["selection_id"],
        created_at=gen_record["created_at"],
        test_cases=gen_record["test_cases"],
        validation_status=gen_record["validation_status"],
        staleness=StalenessInfo(stale=False) # Newly generated cannot be stale
    )

@router.get("/generations/by-selection/{selection_id}", response_model=List[GenerationResponse])
def get_generations_by_selection(selection_id: int, db: Session = Depends(get_db)):
    """
    Retrieve all test case generations associated with a specific selection ID.
    """
    gens = store.get_generations_by_selection(selection_id)
    return [_format_generation_response(db, g) for g in gens]

@router.get("/generations/by-node/{logical_node_id}", response_model=List[GenerationResponse])
def get_generations_by_node(logical_node_id: str, db: Session = Depends(get_db)):
    """
    Retrieve all test case generations associated with a specific logical node.
    """
    gens = store.get_generations_by_node(logical_node_id)
    return [_format_generation_response(db, g) for g in gens]

@router.get("/generations/{generation_id}", response_model=GenerationResponse)
def get_generation(generation_id: str, db: Session = Depends(get_db)):
    """
    Fetch a single generation record by its ID, complete with current staleness status.
    """
    try:
        gen = store.get_generation(generation_id)
        return _format_generation_response(db, gen)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Generation not found")

def _format_generation_response(db: Session, gen_record: dict) -> GenerationResponse:
    staleness = check_staleness(db, gen_record)
    return GenerationResponse(
        generation_id=gen_record["generation_id"],
        selection_id=gen_record.get("selection_id"),
        created_at=gen_record.get("created_at"),
        test_cases=gen_record.get("test_cases", []),
        validation_status=gen_record.get("validation_status", "ok"),
        staleness=staleness
    )
