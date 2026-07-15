"""
tests/conftest.py — Shared pytest fixtures.

Provides a `test_engine` session-scoped fixture and a `db` function-scoped
fixture for direct SQLAlchemy tests (test_ingestion.py).

The API integration tests (test_api_documents_nodes.py) manage their own
fixtures internally because they need module-scoped TestClient lifetime.
"""
# Intentionally minimal — each test file manages its own specialized fixtures.
