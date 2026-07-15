"""
app/db.py — SQLAlchemy engine, session factory, and Base.

Design choices
--------------
- `check_same_thread=False` is required for SQLite when FastAPI uses
  async or multi-threaded test runners; harmless for Postgres.
- `Base` is defined here (not in orm.py) so it can be imported by both
  orm.py and test fixtures without circular imports.
- `init_db()` is a one-shot helper that creates all tables; called at
  app startup and in test fixtures.
- `get_db()` is a FastAPI dependency that yields a scoped session and
  guarantees close even on exception.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import settings

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
_connect_args = (
    {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_connect_args,
    # Echo SQL for debugging — flip to True in dev if needed
    echo=False,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ---------------------------------------------------------------------------
# Declarative base (imported by orm.py)
# ---------------------------------------------------------------------------
Base = declarative_base()


# ---------------------------------------------------------------------------
# Table creation helper
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Create all tables defined under Base.  Safe to call multiple times."""
    # Import orm so all models are registered on Base before create_all
    import app.models.orm  # noqa: F401
    Base.metadata.create_all(bind=engine)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
def get_db():
    """Yield a SQLAlchemy session, closing it after the request."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
