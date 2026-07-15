import difflib
import os
from typing import Dict, Any
from sqlalchemy.orm import Session
from app.models.orm import Node, DocumentVersion
from app.models.schemas import StalenessInfo

def check_staleness(db: Session, generation: Dict[str, Any]) -> StalenessInfo:
    """
    Detects if a generation is stale by comparing its source_snapshot
    against the latest version of the document in the database.
    """
    snapshot = generation.get("source_snapshot", [])
    if not snapshot:
        return StalenessInfo(stale=False, reasons=[], diffs=[])

    # 1. Determine the original node and document
    # We assume all nodes in the generation come from the same document.
    first_node_id = snapshot[0].get("node_id")
    if not first_node_id:
        return StalenessInfo(stale=False, reasons=[], diffs=[])
        
    first_node = db.query(Node).filter(Node.id == first_node_id).first()
    if not first_node:
        # Hard deleted from DB
        return StalenessInfo(stale=True, reasons=["original source section completely deleted from database"], diffs=[])

    original_doc_version = db.query(DocumentVersion).filter(DocumentVersion.id == first_node.document_version_id).first()
    
    # Find the latest version of this document
    latest_version = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == original_doc_version.document_id
    ).order_by(DocumentVersion.version_number.desc()).first()

    if not latest_version:
        return StalenessInfo(stale=True, reasons=["document no longer exists"], diffs=[])
        
    # If the generation was created against the latest version, it's immediately fresh
    if latest_version.id == original_doc_version.id:
        return StalenessInfo(stale=False, reasons=[], diffs=[])

    is_stale = False
    reasons = []
    diffs = []

    # 2. For each entry in snapshot, find corresponding Node in latest version via logical_node_id
    for snap in snapshot:
        logical_node_id = snap["logical_node_id"]
        old_hash = snap["content_hash"]
        old_node_id = snap["node_id"]
        title = snap.get("title", logical_node_id)
        
        latest_node = db.query(Node).filter(
            Node.document_version_id == latest_version.id,
            Node.logical_node_id == logical_node_id
        ).first()

        if not latest_node:
            is_stale = True
            reasons.append(f"source section '{title}' removed in a later version")
        else:
            if latest_node.content_hash != old_hash:
                is_stale = True
                reasons.append(f"source section '{title}' text changed")
                
                # Fetch original node text to generate a diff
                old_node = db.query(Node).filter(Node.id == old_node_id).first()
                if old_node:
                    diff = "\n".join(difflib.unified_diff(
                        old_node.body.splitlines(),
                        latest_node.body.splitlines(),
                        fromfile="old_version",
                        tofile="new_version",
                        n=2 # 2 context lines is enough
                    ))
                    diffs.append(diff)
                else:
                    diffs.append("Original text unavailable for diff.")

    return StalenessInfo(stale=is_stale, reasons=list(set(reasons)), diffs=diffs)
