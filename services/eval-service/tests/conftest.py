"""Shared pytest fixtures.

Points the app at an in-memory sqlite DB (instead of Postgres) and dummy
agent-service/llm-gateway URLs, entirely via environment variables set BEFORE
`app.*` is imported anywhere -- so tests never touch a real database or
network service.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_ON_STARTUP"] = "false"
os.environ["AGENT_SERVICE_URL"] = "http://agent-service.invalid"
os.environ["LLM_GATEWAY_URL"] = "http://llm-gateway.invalid"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_db():
    """Gives every test a clean set of tables on the shared in-memory engine."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _reset_auth_config():
    orig_require_auth = config.REQUIRE_AUTH
    orig_api_key = config.API_KEY
    yield
    config.REQUIRE_AUTH = orig_require_auth
    config.API_KEY = orig_api_key


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client
