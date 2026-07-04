"""
Database engine and session management.

Sync SQLAlchemy is used deliberately for Sprint 1: it is simpler, has
fewer moving parts than an async driver stack, and FastAPI runs sync
path-operation dependencies in a threadpool automatically, so this does
not block the event loop. Revisiting async SQLAlchemy (asyncpg) is a
reasonable Sprint 2+ optimization once request volume warrants it.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings

settings = get_settings()


def _make_engine():
    url = settings.DATABASE_URL
    connect_args = {}
    kwargs = {"pool_pre_ping": True, "future": True}

    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in url:
            # Share the single in-memory DB across all connections/threads
            # within a process — required for TestClient-driven test suites.
            kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = connect_args

    return create_engine(url, **kwargs)


engine = _make_engine()

SessionLocal = sessionmaker(
    bind=engine, autocommit=False, autoflush=False, future=True
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
