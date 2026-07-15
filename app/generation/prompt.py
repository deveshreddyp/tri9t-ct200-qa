from typing import List
from app.models.orm import Node

def build_prompt(nodes: List[Node]) -> str:
    """
    Construct prompt for the LLM based on a selection of document nodes,
    specifying the expected JSON schema and guidelines for medical device QA.
    """
    # Stub implementation - prompt template + builder
    return ""
