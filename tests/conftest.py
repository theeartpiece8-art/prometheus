"""
Shared pytest fixtures.

Per 13_Testing_Strategy.md: "Use deterministic synthetic datasets" and
"Repeatable test environments". Each test function gets a fresh in-memory
SQLite database (all tables created, then dropped after the test) — fully
isolated, no shared state between tests, no external services required.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use-only-testing")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.base import Base
from app.infrastructure.database.session import get_db
import app.infrastructure.models  # noqa: F401 — registers all tables on Base.metadata
from app.infrastructure.security.token_blacklist import reset_token_blacklist_for_tests
from app.main import app


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    reset_token_blacklist_for_tests()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def registered_user(client):
    """Registers a fresh user and returns (client, auth_headers, user_json)."""
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": "trader_jane", "email": "jane@example.com", "password": "S3curePass123"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    return client, headers, body
