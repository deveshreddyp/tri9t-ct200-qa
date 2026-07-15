import json
import uuid
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple, Dict, Any

from fastapi import HTTPException, status
from pydantic import ValidationError

from app.models.orm import Node
from app.models.schemas import TestCaseList
from app.generation.llm_client import generate_test_cases, generate_test_cases_retry
from app.generation.store import save_generation

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
    nodes: List[Node],
    selection_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Executes the generation loop per TRD Section 7:
    1. Call LLM
    2. Try json.loads + validate via Pydantic
    3. On failure, 1 corrective retry with error string
    4. On success, store valid JSON. On persistent failure, store raw text and fail.
    Saves to the Document/JSON store (app.generation.store).
    """
    
    # 1. Prepare input text from nodes
    input_text = "\n\n".join([f"## {n.title}\n{n.body}" for n in nodes])
    
    generation_id = str(uuid.uuid4())
    
    # Pre-populate the snapshot items for staleness tracking later
    source_snapshot = [
        {
            "logical_node_id": node.logical_node_id,
            "node_id": node.id,
            "content_hash": node.content_hash,
            "title": node.title
        }
        for node in nodes
    ]

    gen_record = {
        "generation_id": generation_id,
        "selection_id": selection_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_snapshot": source_snapshot,
        "prompt_version": "v1",
        "llm_provider": "gemini-1.5-pro",
        "llm_raw_response": "",
        "test_cases": [],
        "validation_status": "ok",
        "validation_notes": None
    }
    
    # 2. Call LLM
    try:
        raw_output = await generate_test_cases(input_text)
    except Exception as e:
        # If the API fails entirely, we don't save a Generation file.
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
                gen_record["llm_raw_response"] = retry_output
                gen_record["test_cases"] = parsed_json_retry
                gen_record["validation_status"] = "repaired"
                gen_record["validation_notes"] = f"Repaired after initial failure: {validation_error}"
                save_generation(gen_record)
            else:
                # Retry failed too
                gen_record["llm_raw_response"] = retry_output
                gen_record["validation_status"] = "failed"
                gen_record["validation_notes"] = f"Retry failed: {validation_error_retry} | Original error: {validation_error}"
                save_generation(gen_record)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, 
                    detail="LLM failed to produce valid schema after retry."
                )
        except HTTPException:
            raise
        except Exception as e:
            gen_record["llm_raw_response"] = raw_output
            gen_record["validation_status"] = "failed"
            gen_record["validation_notes"] = f"Retry threw exception: {str(e)} | Original error: {validation_error}"
            save_generation(gen_record)
            raise HTTPException(status_code=500, detail=str(e))
    else:
        # Initial success
        gen_record["llm_raw_response"] = raw_output
        gen_record["test_cases"] = parsed_json
        gen_record["validation_status"] = "ok"
        save_generation(gen_record)

    return gen_record

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
