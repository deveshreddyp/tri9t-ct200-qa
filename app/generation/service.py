import json
import uuid
import re
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from pydantic import ValidationError

from app.models.orm import Generation, GenerationSnapshotItem, Node, Selection
from app.models.schemas import TestCaseList
from app.generation.llm_client import generate_test_cases, generate_test_cases_retry

def _strip_markdown_fences(text: str) -> str:
    """Defensively strip ```json ... ``` markdown fences from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        # find the first newline to skip ```json
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline+1:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

async def generate_and_store_test_cases(
    db: Session,
    nodes: List[Node],
    selection_id: Optional[int] = None
) -> Generation:
    """
    Executes the generation loop per TRD Section 7:
    1. Call LLM
    2. Try json.loads + validate via Pydantic
    3. On failure, 1 corrective retry with error string
    4. On success, store valid JSON. On persistent failure, store raw text and fail.
    """
    
    # 1. Prepare input text from nodes
    input_text = "\n\n".join([f"## {n.title}\n{n.body}" for n in nodes])
    
    generation_id = str(uuid.uuid4())
    gen = Generation(
        id=generation_id,
        selection_id=selection_id,
        validation_status="ok",
        raw_response=""
    )
    db.add(gen)
    
    # Pre-populate the snapshot items for staleness tracking later
    for node in nodes:
        snap = GenerationSnapshotItem(
            generation_id=generation_id,
            logical_node_id=node.logical_node_id,
            content_hash=node.content_hash
        )
        db.add(snap)
        
    db.flush() # ensure PKs exist

    # 2. Call LLM
    try:
        raw_output = await generate_test_cases(input_text)
    except Exception as e:
        # If the API fails entirely (network, auth, etc.), we don't save a Generation row.
        raise HTTPException(status_code=500, detail=str(e))

    # 3. Attempt parse and validate
    parsed_json, validation_error = _parse_and_validate(raw_output)
    
    if validation_error:
        # 4. Corrective retry
        try:
            retry_output = await generate_test_cases_retry(input_text, validation_error, raw_output)
            parsed_json_retry, validation_error_retry = _parse_and_validate(retry_output)
            
            if not validation_error_retry:
                # Retry succeeded!
                gen.raw_response = retry_output
                gen.test_cases_json = json.dumps(parsed_json_retry)
                gen.validation_status = "repaired"
                gen.validation_notes = f"Repaired after initial failure: {validation_error}"
            else:
                # Retry failed too
                gen.raw_response = retry_output
                gen.validation_status = "failed"
                gen.validation_notes = f"Retry failed: {validation_error_retry} | Original error: {validation_error}"
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, 
                    detail="LLM failed to produce valid schema after retry."
                )
        except HTTPException:
            raise
        except Exception as e:
            gen.raw_response = raw_output
            gen.validation_status = "failed"
            gen.validation_notes = f"Retry threw exception: {str(e)} | Original error: {validation_error}"
            db.commit()
            raise HTTPException(status_code=500, detail=str(e))
    else:
        # Initial success
        gen.raw_response = raw_output
        gen.test_cases_json = json.dumps(parsed_json)
        gen.validation_status = "ok"

    db.commit()
    db.refresh(gen)
    return gen

def _parse_and_validate(raw_text: str) -> Tuple[Optional[list], Optional[str]]:
    """Helper to parse raw LLM text and validate it through Pydantic."""
    cleaned = _strip_markdown_fences(raw_text)
    
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return None, f"JSON Decode Error: {str(e)}"
        
    try:
        validated = TestCaseList.model_validate(data)
        # RootModel data access
        return [tc.model_dump() for tc in validated.root], None
    except ValidationError as e:
        return None, f"Schema Validation Error: {str(e)}"
