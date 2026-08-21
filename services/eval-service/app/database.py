"""SQLAlchemy engine/session setup.

Uses Postgres in normal operation (DATABASE_URL). Also transparently supports
a `sqlite://` DATABASE_URL (used by the test suite) so the same engine/session
plumbing is exercised in tests as in production, instead of a parallel test-only
setup.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import DATABASE_URL

_engine_kwargs: dict = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    # In-memory/sqlite: use a StaticPool so all sessions share the same
    # underlying connection (otherwise an in-memory DB resets per-connection),
    # and allow cross-thread use since TestClient may run in another thread.
    _engine_kwargs = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency: yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
