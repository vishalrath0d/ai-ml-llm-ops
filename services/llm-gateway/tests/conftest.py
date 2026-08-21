"""Shared pytest fixtures.

No test in this suite makes a real network call or needs a real API key --
provider `.generate` / `.generate_stream` methods are monkeypatched per-test.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import chaos_state, settings
from app.main import app
from app.rate_limit import reset_windows


@pytest.fixture(autouse=True)
def reset_state():
    """Ensure chaos settings and provider config don't leak between tests."""
    orig_latency = chaos_state.latency_ms
    orig_error_rate = chaos_state.error_rate
    orig_chain = list(settings.LLM_PROVIDER_CHAIN)
    orig_require_auth = settings.REQUIRE_AUTH
    orig_api_key = settings.API_KEY
    orig_rate_limit = settings.RATE_LIMIT_PER_MINUTE

    chaos_state.latency_ms = 0
    chaos_state.error_rate = 0.0
    reset_windows()

    yield

    chaos_state.latency_ms = orig_latency
    chaos_state.error_rate = orig_error_rate
    settings.LLM_PROVIDER_CHAIN = orig_chain
    settings.REQUIRE_AUTH = orig_require_auth
    settings.API_KEY = orig_api_key
    settings.RATE_LIMIT_PER_MINUTE = orig_rate_limit


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
