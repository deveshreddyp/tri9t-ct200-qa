import difflib
from typing import Dict, List, Any, Optional

from app.models.orm import Node
from app.parser.pdf_tree import ParsedNode

def _build_path_keys(nodes: List[Any], id_attr: str, parent_id_attr: str) -> Dict[Any, str]:
    """
    Builds unique path keys for each node to handle duplicate headings.
    nodes must be sorted in document order (order_index).
    Returns a dict mapping node ID to its path key.
    """
    node_paths: Dict[Any, str] = {}
    # (parent_id, title) -> count of occurrences so far
    title_counts: Dict[tuple, int] = {}

    for n in nodes:
        node_id = getattr(n, id_attr)
        parent_id = getattr(n, parent_id_attr)
        title = n.title

        idx = title_counts.get((parent_id, title), 0)
        title_counts[(parent_id, title)] = idx + 1
        
        indexed_title = f"{title}[{idx}]"
        
        parent_path = node_paths.get(parent_id, "") if parent_id is not None else ""
        path_key = f"{parent_path} > {indexed_title}" if parent_path else indexed_title
        
        node_paths[node_id] = path_key

    return node_paths

def match_version_nodes(v1_nodes: List[Node], v2_nodes: List[ParsedNode]) -> None:
    """
    Match ParsedNodes from a new version (v2) against existing ORM Nodes (v1).
    Mutates v2_nodes by replacing their newly generated logical_node_id with
    the matching logical_node_id from v1, if a match is found.
    """
    if not v1_nodes or not v2_nodes:
        return

    # 1. Build path keys
    v1_paths = _build_path_keys(
        sorted(v1_nodes, key=lambda n: n.order_index),
        id_attr="id",
        parent_id_attr="parent_id"
    )
    v2_paths = _build_path_keys(
        sorted(v2_nodes, key=lambda n: n.order_index),
        id_attr="node_id",
        parent_id_attr="parent_id"
    )

    # Reverse map for v1: path_key -> Node
    v1_by_path = {path: n for n_id, path in v1_paths.items() for n in v1_nodes if n.id == n_id}
    v1_matched_ids = set()
    
    # Map to track which V1 node a V2 node matched to, to help with fuzzy fallback
    # v2_node_id -> v1_node.id
    v2_to_v1_match: Dict[str, int] = {}

    # Phase 1: Exact Path Matching
    unmatched_v2 = []
    for v2_n in v2_nodes:
        path = v2_paths[v2_n.node_id]
        v1_n = v1_by_path.get(path)
        if v1_n and v1_n.id not in v1_matched_ids:
            # Match!
            v2_n.logical_node_id = v1_n.logical_node_id
            v1_matched_ids.add(v1_n.id)
            v2_to_v1_match[v2_n.node_id] = v1_n.id
        else:
            unmatched_v2.append(v2_n)

    # Phase 2: Fuzzy Title Matching Fallback
    # Group unmatched V1 nodes by their parent
    v1_unmatched_by_parent: Dict[Optional[int], List[Node]] = {}
    for v1_n in v1_nodes:
        if v1_n.id not in v1_matched_ids:
            v1_unmatched_by_parent.setdefault(v1_n.parent_id, []).append(v1_n)

    for v2_n in unmatched_v2:
        # Find the expected V1 parent for this V2 node
        expected_v1_parent_id = None
        if v2_n.parent_id is not None:
            # If the V2 parent was matched to a V1 node, look at that V1 node's children
            expected_v1_parent_id = v2_to_v1_match.get(v2_n.parent_id)
            if expected_v1_parent_id is None:
                # Parent didn't match anything, so fuzzy matching this child is unsafe
                continue

        candidates = v1_unmatched_by_parent.get(expected_v1_parent_id, [])
        if not candidates:
            continue

        best_match = None
        best_ratio = 0.0

        for cand in candidates:
            ratio = difflib.SequenceMatcher(None, v2_n.title, cand.title).ratio()
            if ratio >= 0.8 and ratio > best_ratio:
                best_ratio = ratio
                best_match = cand

        if best_match:
            # Fuzzy Match!
            v2_n.logical_node_id = best_match.logical_node_id
            v1_matched_ids.add(best_match.id)
            v2_to_v1_match[v2_n.node_id] = best_match.id
            candidates.remove(best_match)
