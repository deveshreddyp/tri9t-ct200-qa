import json
import os
from typing import Dict, Any, List

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GENERATIONS_DIR = os.path.join(_REPO_ROOT, "data", "generations")

def _ensure_dir():
    os.makedirs(GENERATIONS_DIR, exist_ok=True)

def save_generation(gen_record: Dict[str, Any]):
    """
    Saves a generation record as a JSON file under data/generations/{generation_id}.json
    """
    _ensure_dir()
    gen_id = gen_record["generation_id"]
    path = os.path.join(GENERATIONS_DIR, f"{gen_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(gen_record, f, indent=2)

def get_generation(generation_id: str) -> Dict[str, Any]:
    """
    Retrieves a generation record by its ID.
    Raises FileNotFoundError if it doesn't exist.
    """
    path = os.path.join(GENERATIONS_DIR, f"{generation_id}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def list_generations() -> List[Dict[str, Any]]:
    """
    Returns all generation records.
    """
    _ensure_dir()
    results = []
    for filename in os.listdir(GENERATIONS_DIR):
        if filename.endswith(".json"):
            path = os.path.join(GENERATIONS_DIR, filename)
            with open(path, "r", encoding="utf-8") as f:
                results.append(json.load(f))
    # Sort by created_at descending
    results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return results

def get_generations_by_selection(selection_id: int) -> List[Dict[str, Any]]:
    """
    Returns all generation records for a specific selection_id.
    """
    all_gens = list_generations()
    return [g for g in all_gens if g.get("selection_id") == selection_id]

def get_generations_by_node(logical_node_id: str) -> List[Dict[str, Any]]:
    """
    Returns all generation records that include the specified logical_node_id in their source_snapshot.
    """
    all_gens = list_generations()
    results = []
    for g in all_gens:
        snapshot = g.get("source_snapshot", [])
        if any(snap.get("logical_node_id") == logical_node_id for snap in snapshot):
            results.append(g)
    return results
