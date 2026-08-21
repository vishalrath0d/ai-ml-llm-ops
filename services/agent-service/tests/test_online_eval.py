"""Tests for app/online_eval.py -- the sampled, asynchronous LLM-as-judge
scoring of live /chat traffic (see that module's docstring for how this
differs from eval-service's offline scenario judge).

No real network call and no real Langfuse instance: the judge call goes
through `httpx.post`, which is monkeypatched here, and the Langfuse client
is replaced with a fake that just records what it was called with.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app import online_eval


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeLangfuseClient:
    def __init__(self):
        self.scores: list[dict[str, Any]] = []
        self.flushed = False

    def create_score(self, **kwargs):
        self.scores.append(kwargs)

    def flush(self):
        self.flushed = True


def _judge_payload(score_percent: float, reason: str = "looks fine") -> dict:
    return {
        "choices": [
            {"message": {"content": json.dumps({"score_percent": score_percent, "reason": reason})}}
        ]
    }


def test_is_sampled_respects_rate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(online_eval, "ONLINE_EVAL_SAMPLE_RATE", 0.5)
    monkeypatch.setattr(online_eval.random, "random", lambda: 0.4)
    assert online_eval.is_sampled() is True

    monkeypatch.setattr(online_eval.random, "random", lambda: 0.6)
    assert online_eval.is_sampled() is False


def test_run_online_eval_writes_score_to_langfuse(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FakeLangfuseClient()
    monkeypatch.setattr(online_eval, "get_langfuse_client", lambda: fake_client)
    monkeypatch.setattr(
        online_eval.httpx, "post", lambda *a, **kw: _FakeResponse(_judge_payload(85, "coherent and on-topic"))
    )

    online_eval.run_online_eval("trace-123", "what plan am I on?", "You're on the Pro plan.")

    assert len(fake_client.scores) == 1
    score = fake_client.scores[0]
    assert score["trace_id"] == "trace-123"
    assert score["value"] == 85.0
    assert score["data_type"] == "NUMERIC"
    assert "coherent" in score["comment"]
    assert fake_client.flushed is True


def test_run_online_eval_tolerates_prose_wrapped_json(monkeypatch: pytest.MonkeyPatch):
    """A small local model sometimes wraps the requested JSON in prose --
    the judge-output parser should still pull the score out."""
    fake_client = _FakeLangfuseClient()
    monkeypatch.setattr(online_eval, "get_langfuse_client", lambda: fake_client)
    wrapped = 'Sure, here is my evaluation:\n```json\n{"score_percent": 60, "reason": "a bit terse"}\n```'
    monkeypatch.setattr(
        online_eval.httpx,
        "post",
        lambda *a, **kw: _FakeResponse({"choices": [{"message": {"content": wrapped}}]}),
    )

    online_eval.run_online_eval("trace-456", "hi", "hello")

    assert fake_client.scores[0]["value"] == 60.0


def test_run_online_eval_never_raises_on_malformed_judge_output(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FakeLangfuseClient()
    monkeypatch.setattr(online_eval, "get_langfuse_client", lambda: fake_client)
    monkeypatch.setattr(
        online_eval.httpx,
        "post",
        lambda *a, **kw: _FakeResponse({"choices": [{"message": {"content": "not json at all"}}]}),
    )

    online_eval.run_online_eval("trace-789", "hi", "hello")  # must not raise

    assert fake_client.scores == []


def test_run_online_eval_never_raises_when_judge_call_itself_fails(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FakeLangfuseClient()
    monkeypatch.setattr(online_eval, "get_langfuse_client", lambda: fake_client)

    def _boom(*a, **kw):
        raise ConnectionError("llm-gateway unreachable")

    monkeypatch.setattr(online_eval.httpx, "post", _boom)

    online_eval.run_online_eval("trace-000", "hi", "hello")  # must not raise

    assert fake_client.scores == []


def test_run_online_eval_still_records_metric_when_langfuse_unavailable(monkeypatch: pytest.MonkeyPatch):
    """If Langfuse itself isn't configured/reachable, the score is simply not
    attached anywhere -- it must not crash the (already-sent) request."""
    monkeypatch.setattr(online_eval, "get_langfuse_client", lambda: None)
    monkeypatch.setattr(online_eval.httpx, "post", lambda *a, **kw: _FakeResponse(_judge_payload(70)))

    online_eval.run_online_eval("trace-111", "hi", "hello")  # must not raise
