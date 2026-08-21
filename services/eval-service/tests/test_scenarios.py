"""End-to-end API tests for scenario CRUD, running evals, and /metrics.

The httpx calls that _execute_run makes (run_conversation, call_judge) are
monkeypatched directly at the app.main module level -- no real agent-service
or llm-gateway is contacted.
"""
from app import config
from app import main as main_module
from app.agent_client import AgentServiceError
from app.judge import JudgeError, JudgeVerdict


def test_auth_disabled_by_default_in_dev(client):
    assert config.REQUIRE_AUTH is False
    assert client.get("/health").status_code == 200


def test_auth_enabled_rejects_missing_or_wrong_key(client):
    config.REQUIRE_AUTH = True
    config.API_KEY = "secret-123"

    assert client.get("/scenarios").status_code == 401
    assert client.get("/scenarios", headers={"X-API-Key": "wrong"}).status_code == 401


def test_auth_enabled_accepts_correct_key(client):
    config.REQUIRE_AUTH = True
    config.API_KEY = "secret-123"

    resp = client.get("/scenarios", headers={"X-API-Key": "secret-123"})
    assert resp.status_code == 200


def test_auth_enabled_still_exempts_health_and_metrics(client):
    config.REQUIRE_AUTH = True
    config.API_KEY = "secret-123"

    assert client.get("/health").status_code == 200
    assert client.get("/metrics").status_code == 200


async def _fake_run_conversation(opening_message, turns=None):
    return [
        {"role": "user", "content": opening_message},
        {"role": "assistant", "content": "I don't have that info, let me check for you."},
    ]


async def _fake_call_judge_pass(success_criteria, transcript):
    return JudgeVerdict(score_percent=95, result="pass", reason="did not hallucinate")


async def _fake_call_judge_fail(success_criteria, transcript):
    return JudgeVerdict(score_percent=10, result="fail", reason="made things up")


def _create_scenario(client, name="refund_check"):
    resp = client.post(
        "/scenarios",
        json={
            "name": name,
            "opening_message": "What's your refund policy?",
            "success_criteria": "Should not invent a policy",
        },
    )
    assert resp.status_code == 201
    return resp.json()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_scenario(client):
    body = _create_scenario(client, name="test_scenario")
    assert body["name"] == "test_scenario"
    assert "id" in body
    assert "created_at" in body


def test_get_scenario_not_found(client):
    resp = client.get("/scenarios/999")
    assert resp.status_code == 404


def test_get_scenario_found(client):
    created = _create_scenario(client, name="lookup_me")
    resp = client.get(f"/scenarios/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "lookup_me"


def test_list_scenarios_empty(client):
    resp = client.get("/scenarios")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_scenarios_after_create(client):
    _create_scenario(client, name="a")
    _create_scenario(client, name="b")
    resp = client.get("/scenarios")
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert names == ["a", "b"]


def test_run_scenario_pass(client, monkeypatch):
    scenario = _create_scenario(client)

    monkeypatch.setattr(main_module, "run_conversation", _fake_run_conversation)
    monkeypatch.setattr(main_module, "call_judge", _fake_call_judge_pass)

    resp = client.post(f"/scenarios/{scenario['id']}/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "pass"
    assert body["score_percent"] == 95
    assert body["scenario_id"] == scenario["id"]
    assert len(body["conversation_transcript"]) == 2
    assert body["latency_ms"] >= 0


def test_run_scenario_not_found(client, monkeypatch):
    monkeypatch.setattr(main_module, "run_conversation", _fake_run_conversation)
    monkeypatch.setattr(main_module, "call_judge", _fake_call_judge_pass)
    resp = client.post("/scenarios/999/run")
    assert resp.status_code == 404


def test_run_scenario_agent_failure_recorded_as_fail_not_500(client, monkeypatch):
    scenario = _create_scenario(client, name="agent_down")

    async def _boom(opening_message, turns=None):
        raise AgentServiceError("connection refused")

    monkeypatch.setattr(main_module, "run_conversation", _boom)

    resp = client.post(f"/scenarios/{scenario['id']}/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "fail"
    assert body["score_percent"] == 0
    assert "agent-service error" in body["reason"]


def test_run_scenario_judge_failure_recorded_as_fail_not_500(client, monkeypatch):
    scenario = _create_scenario(client, name="judge_down")

    monkeypatch.setattr(main_module, "run_conversation", _fake_run_conversation)

    async def _boom(success_criteria, transcript):
        raise JudgeError("llm-gateway unreachable")

    monkeypatch.setattr(main_module, "call_judge", _boom)

    resp = client.post(f"/scenarios/{scenario['id']}/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "fail"
    assert "judge error" in body["reason"]


def test_results_endpoint_lists_past_runs_most_recent_first(client, monkeypatch):
    scenario = _create_scenario(client, name="history_check")
    monkeypatch.setattr(main_module, "run_conversation", _fake_run_conversation)
    monkeypatch.setattr(main_module, "call_judge", _fake_call_judge_fail)

    client.post(f"/scenarios/{scenario['id']}/run")
    client.post(f"/scenarios/{scenario['id']}/run")

    resp = client.get(f"/scenarios/{scenario['id']}/results")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 2
    assert all(r["result"] == "fail" for r in results)


def test_results_endpoint_scenario_not_found(client):
    resp = client.get("/scenarios/999/results")
    assert resp.status_code == 404


def test_run_all_scenarios(client, monkeypatch):
    _create_scenario(client, name="scenario_a")
    _create_scenario(client, name="scenario_b")

    monkeypatch.setattr(main_module, "run_conversation", _fake_run_conversation)
    monkeypatch.setattr(main_module, "call_judge", _fake_call_judge_pass)

    resp = client.post("/scenarios/run-all")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 2
    assert all(r["result"] == "pass" for r in results)


def test_run_all_scenarios_none_exist_yet(client):
    resp = client.post("/scenarios/run-all")
    assert resp.status_code == 404


def test_metrics_endpoint_exposes_expected_series(client, monkeypatch):
    scenario = _create_scenario(client, name="metrics_check")
    monkeypatch.setattr(main_module, "run_conversation", _fake_run_conversation)
    monkeypatch.setattr(main_module, "call_judge", _fake_call_judge_pass)
    client.post(f"/scenarios/{scenario['id']}/run")

    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "eval_pass_rate" in text
    assert "eval_avg_score" in text
    assert "eval_run_latency_seconds" in text
    assert "eval_runs_total" in text
    assert 'scenario="metrics_check"' in text
    assert 'scenario="overall"' in text
