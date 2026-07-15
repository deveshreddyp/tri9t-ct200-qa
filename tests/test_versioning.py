import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import orm
from app.ingestion.service import ingest_pdf
from app.parser.pdf_tree import ParsedNode
from app.versioning.matcher import match_version_nodes, _build_path_keys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_V1 = os.path.join(_REPO_ROOT, "data", "ct200_manual.pdf")
PDF_V2 = os.path.join(_REPO_ROOT, "data", "ct200_manual_v2.pdf")

skip_no_pdf = pytest.mark.skipif(
    not os.path.exists(PDF_V1) or not os.path.exists(PDF_V2),
    reason="PDFs not present; skipping integration tests",
)

@pytest.fixture(scope="module")
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()

@pytest.fixture(scope="module")
def ingested_docs(db_session):
    if not os.path.exists(PDF_V1) or not os.path.exists(PDF_V2):
        pytest.skip("PDFs not present")
        
    res1 = ingest_pdf(db_session, PDF_V1, "CT-200")
    res2 = ingest_pdf(db_session, PDF_V2, "CT-200")
    db_session.commit()
    return res1, res2

@skip_no_pdf
def test_unchanged_node_keeps_same_logical_id(db_session, ingested_docs):
    res1, res2 = ingested_docs
    
    # "1.1 Intended Use" should be completely identical in both versions
    v1_node = db_session.query(orm.Node).filter_by(
        document_version_id=res1.version_id, title="1.1 Intended Use"
    ).first()
    v2_node = db_session.query(orm.Node).filter_by(
        document_version_id=res2.version_id, title="1.1 Intended Use"
    ).first()
    
    assert v1_node is not None
    assert v2_node is not None
    assert v1_node.logical_node_id == v2_node.logical_node_id
    assert v1_node.content_hash == v2_node.content_hash

@skip_no_pdf
def test_changed_body_detected_via_hash(db_session, ingested_docs):
    res1, res2 = ingested_docs
    
    # "2.1.1.1 Battery Life Under Typical Use" body changed from 300 to 250
    v1_node = db_session.query(orm.Node).filter_by(
        document_version_id=res1.version_id, title="2.1.1.1 Battery Life Under Typical Use"
    ).first()
    v2_node = db_session.query(orm.Node).filter_by(
        document_version_id=res2.version_id, title="2.1.1.1 Battery Life Under Typical Use"
    ).first()
    
    assert v1_node is not None
    assert v2_node is not None
    # Still the same logical node
    assert v1_node.logical_node_id == v2_node.logical_node_id
    # But the content hash is different
    assert v1_node.content_hash != v2_node.content_hash
    assert "300" in v1_node.body
    assert "250" in v2_node.body

@skip_no_pdf
def test_removed_or_added_nodes_flagged(db_session, ingested_docs):
    res1, res2 = ingested_docs
    
    # "5.3 Data Export" is in v2 but NOT in v1
    v1_node = db_session.query(orm.Node).filter_by(
        document_version_id=res1.version_id, title="5.3 Data Export"
    ).first()
    v2_node = db_session.query(orm.Node).filter_by(
        document_version_id=res2.version_id, title="5.3 Data Export"
    ).first()
    
    assert v1_node is None
    assert v2_node is not None
    
    # Ensure this new logical ID doesn't exist anywhere in v1
    v1_check = db_session.query(orm.Node).filter_by(
        document_version_id=res1.version_id, logical_node_id=v2_node.logical_node_id
    ).first()
    assert v1_check is None

def test_duplicate_numbering_resolved_correctly():
    """
    Unit test for the matching logic on duplicate headings.
    If we have two sibling headings with the exact same title, they
    should be matched by order.
    """
    class MockNode:
        def __init__(self, id, parent_id, title, order_index, logical_node_id=None):
            self.id = id
            self.node_id = id
            self.parent_id = parent_id
            self.title = title
            self.order_index = order_index
            self.logical_node_id = logical_node_id
            
    v1_nodes = [
        MockNode("root", None, "Root", 0, "log_root"),
        MockNode("c1", "root", "Duplicate", 1, "log_dup1"),
        MockNode("c2", "root", "Duplicate", 2, "log_dup2"),
    ]
    
    # V2 has the same structure, but new raw IDs
    v2_nodes = [
        MockNode("new_root", None, "Root", 0, "new_log_root"),
        MockNode("new_c1", "new_root", "Duplicate", 1, "new_log_dup1"),
        MockNode("new_c2", "new_root", "Duplicate", 2, "new_log_dup2"),
    ]
    
    # Manually map parents for the test
    v2_nodes[1].parent_id = "new_root"
    v2_nodes[2].parent_id = "new_root"
    
    match_version_nodes(v1_nodes, v2_nodes)
    
    assert v2_nodes[0].logical_node_id == "log_root"
    # c1 matches dup1
    assert v2_nodes[1].logical_node_id == "log_dup1"
    # c2 matches dup2
    assert v2_nodes[2].logical_node_id == "log_dup2"

def test_fuzzy_matching():
    """
    Unit test for fuzzy matching logic.
    A reworded title should still match if it's under the same parent and highly similar.
    """
    class MockNode:
        def __init__(self, id, parent_id, title, order_index, logical_node_id=None):
            self.id = id
            self.node_id = id
            self.parent_id = parent_id
            self.title = title
            self.order_index = order_index
            self.logical_node_id = logical_node_id
            
    v1_nodes = [
        MockNode("root", None, "Root", 0, "log_root"),
        MockNode("c1", "root", "Some Long Specific Title Here", 1, "log_c1"),
    ]
    
    # V2 has a slightly modified title
    v2_nodes = [
        MockNode("new_root", None, "Root", 0, "new_log_root"),
        MockNode("new_c1", "new_root", "Some Long Specific Title Here Modified", 1, "new_log_c1"),
    ]
    
    match_version_nodes(v1_nodes, v2_nodes)
    
    assert v2_nodes[0].logical_node_id == "log_root"
    # Should be fuzzy matched
    assert v2_nodes[1].logical_node_id == "log_c1"
