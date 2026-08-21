"""Test fixtures.

Critically, this sets CHROMA_PERSIST_DIR to a fresh temp directory and
disables auto-seeding *before* anything under app/ is imported, since
app/config.py reads env vars at import time.
"""
import os
import tempfile

_TMP_CHROMA_DIR = tempfile.mkdtemp(prefix="rag_service_test_chroma_")
os.environ.setdefault("CHROMA_PERSIST_DIR", _TMP_CHROMA_DIR)
os.environ.setdefault("AUTO_SEED_ON_STARTUP", "false")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _reset_auth_config():
    """`client` is session-scoped, so a test that flips config.REQUIRE_AUTH/
    API_KEY would otherwise leak into every later test in the session."""
    orig_require_auth = config.REQUIRE_AUTH
    orig_api_key = config.API_KEY
    yield
    config.REQUIRE_AUTH = orig_require_auth
    config.API_KEY = orig_api_key
