"""Tests for the FastAPI HTTP surface (app/main.py): /health, /metrics, /chat,
and the MLflow-registry admin endpoints.

The LangGraph agent itself is exercised in test_graph.py; here we only
verify the HTTP contract, session persistence, and metrics/tool_calls/
urgency-classification plumbing, by monkeypatching app.main.run_agent_turn
and app.main.classify_urgency directly (never touching a real MLflow
server — that integration is exercised via train_and_log.py + a live
docker-compose stack, not unit tests).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import config

client = TestClient(main_module.app)


@pytest.fixture(autouse=True)
def _reset_auth_config():
    orig_require_auth = config.REQUIRE_AUTH
    orig_api_key = config.API_KEY
    yield
    config.REQUIRE_AUTH = orig_require_auth
    config.API_KEY = orig_api_key


def _patch_urgency(monkeypatch, label="unknown", version=None):
    monkeypatch.setattr(main_module, "classify_urgency", lambda text, customer_id=None: (label, version))


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_metrics_exposes_prometheus_text_format():
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "agent_service_chat_requests_total" in resp.text


def test_auth_disabled_by_default_in_dev():
    assert config.REQUIRE_AUTH is False
    # /health alone proves the auth layer isn't blocking -- deliberately
    # not hitting /chat unmocked here, that would make a real (slow,
    # retried) network call to a llm-gateway that isn't running in a unit
    # test environment, which is a fixture/mocking concern unrelated to
    # what this test is actually about.
    assert client.get("/health").status_code == 200


def test_auth_enabled_rejects_missing_or_wrong_key():
    config.REQUIRE_AUTH = True
    config.API_KEY = "secret-123"

    assert client.post("/chat", json={"session_id": "s", "message": "hi"}).status_code == 401
    resp = client.post(
        "/chat", json={"session_id": "s", "message": "hi"}, headers={"X-API-Key": "wrong"}
    )
    assert resp.status_code == 401


def test_auth_enabled_accepts_correct_key(monkeypatch):
    config.REQUIRE_AUTH = True
    config.API_KEY = "secret-123"

    def fake_run_agent_turn(session_id, history, user_message, extra_system_note=None):
        return "ok", [], history

    monkeypatch.setattr(main_module, "run_agent_turn", fake_run_agent_turn)
    _patch_urgency(monkeypatch)
    main_module._SESSIONS.clear()

    resp = client.post(
        "/chat", json={"session_id": "s", "message": "hi"}, headers={"X-API-Key": "secret-123"}
    )
    assert resp.status_code == 200


def test_auth_enabled_still_exempts_health_and_metrics():
    config.REQUIRE_AUTH = True
    config.API_KEY = "secret-123"

    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200


def test_chat_returns_response_tool_calls_and_session_id(monkeypatch):
    def fake_run_agent_turn(session_id, history, user_message, extra_system_note=None):
        return "mocked answer", ["crm_lookup"], history + [f"human:{user_message}", "ai:mocked answer"]

    monkeypatch.setattr(main_module, "run_agent_turn", fake_run_agent_turn)
    _patch_urgency(monkeypatch)
    main_module._SESSIONS.clear()

    resp = client.post("/chat", json={"session_id": "s-1", "message": "hi"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "response": "mocked answer",
        "tool_calls": ["crm_lookup"],
        "session_id": "s-1",
        "urgency": "unknown",
        "urgency_model_version": None,
    }


def test_chat_persists_history_per_session(monkeypatch):
    calls = []

    def fake_run_agent_turn(session_id, history, user_message, extra_system_note=None):
        calls.append(list(history))
        new_history = history + [user_message]
        return f"echo:{user_message}", [], new_history

    monkeypatch.setattr(main_module, "run_agent_turn", fake_run_agent_turn)
    _patch_urgency(monkeypatch)
    main_module._SESSIONS.clear()

    client.post("/chat", json={"session_id": "s-2", "message": "first"})
    client.post("/chat", json={"session_id": "s-2", "message": "second"})

    assert calls[0] == []
    assert calls[1] == ["first"]


def test_chat_isolates_different_sessions(monkeypatch):
    def fake_run_agent_turn(session_id, history, user_message, extra_system_note=None):
        return "ok", [], history + [user_message]

    monkeypatch.setattr(main_module, "run_agent_turn", fake_run_agent_turn)
    _patch_urgency(monkeypatch)
    main_module._SESSIONS.clear()

    client.post("/chat", json={"session_id": "a", "message": "m1"})
    client.post("/chat", json={"session_id": "b", "message": "m2"})

    assert main_module._SESSIONS["a"] == ["m1"]
    assert main_module._SESSIONS["b"] == ["m2"]


def test_chat_injects_urgency_hint_when_flagged_urgent(monkeypatch):
    """When classify_urgency says "urgent", run_agent_turn must receive a
    non-empty extra_system_note; when "normal", it must receive None."""
    captured = {}

    def fake_run_agent_turn(session_id, history, user_message, extra_system_note=None):
        captured["extra_system_note"] = extra_system_note
        return "ok", [], history + [user_message]

    monkeypatch.setattr(main_module, "run_agent_turn", fake_run_agent_turn)
    main_module._SESSIONS.clear()

    _patch_urgency(monkeypatch, label="urgent", version="3")
    resp = client.post("/chat", json={"session_id": "u-1", "message": "this is broken again!"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["urgency"] == "urgent"
    assert body["urgency_model_version"] == "3"
    assert captured["extra_system_note"]  # non-empty hint was injected

    _patch_urgency(monkeypatch, label="normal", version="3")
    resp = client.post("/chat", json={"session_id": "u-2", "message": "how do I reset my password"})
    assert resp.json()["urgency"] == "normal"
    assert captured["extra_system_note"] is None


def test_chat_forwards_customer_id_to_classify_urgency(monkeypatch):
    """customer_id is what lets classify_urgency pull live Feast features
    for the right customer (see app/model_registry.py) -- if this stops
    being forwarded, urgency classification silently degrades to
    default/neutral features for everyone, no error, just wrong answers."""
    captured = {}

    def fake_classify_urgency(text, customer_id=None):
        captured["text"] = text
        captured["customer_id"] = customer_id
        return "normal", "1"

    def fake_run_agent_turn(session_id, history, user_message, extra_system_note=None):
        return "ok", [], history + [user_message]

    monkeypatch.setattr(main_module, "classify_urgency", fake_classify_urgency)
    monkeypatch.setattr(main_module, "run_agent_turn", fake_run_agent_turn)
    main_module._SESSIONS.clear()

    client.post("/chat", json={"session_id": "c-1", "message": "hello", "customer_id": "cust_002"})
    assert captured == {"text": "hello", "customer_id": "cust_002"}

    client.post("/chat", json={"session_id": "c-2", "message": "hello again"})
    assert captured["customer_id"] is None


def test_admin_model_status_reflects_current_status(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "current_status",
        lambda: {"model_name": "support-urgency-classifier", "alias": "champion", "loaded_version": "2"},
    )
    resp = client.get("/admin/model-status")
    assert resp.status_code == 200
    assert resp.json()["loaded_version"] == "2"


def test_admin_reload_model_delegates_to_force_reload(monkeypatch):
    monkeypatch.setattr(main_module, "force_reload", lambda: {"loaded": True, "version": "5"})
    resp = client.post("/admin/reload-model")
    assert resp.status_code == 200
    assert resp.json() == {"loaded": True, "version": "5"}
