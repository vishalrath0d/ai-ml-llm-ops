"""Tests for the LLM-as-judge prompt building and strict-JSON parsing logic,
including the pattern of extracting JSON from a chatty/non-strict response.

The httpx calls to llm-gateway are mocked with httpx.MockTransport -- no real
network access, no real llm-gateway needed.
"""
import httpx
import pytest

from app.judge import JudgeError, build_judge_messages, call_judge, parse_judge_response

_RealAsyncClient = httpx.AsyncClient


def _patch_async_client(monkeypatch, transport: httpx.MockTransport) -> None:
    """Points app.judge's httpx.AsyncClient at a mocked transport.

    Uses the real AsyncClient class captured above (not a fresh `httpx.AsyncClient`
    lookup) because monkeypatch.setattr("app.judge.httpx.AsyncClient", ...) patches
    the actual shared `httpx` module object (app.judge's `httpx` name IS the global
    httpx module) -- looking it up again inside the replacement would just call the
    replacement itself and recurse.
    """
    monkeypatch.setattr(
        "app.judge.httpx.AsyncClient",
        lambda *a, **kw: _RealAsyncClient(*a, transport=transport, **kw),
    )


def test_parse_judge_response_valid_json():
    raw = '{"score_percent": 92, "result": "pass", "reason": "Agent correctly declined to invent a policy."}'
    verdict = parse_judge_response(raw)
    assert verdict.score_percent == 92
    assert verdict.result == "pass"
    assert "policy" in verdict.reason


def test_parse_judge_response_strips_surrounding_prose():
    raw = 'Sure, here is my verdict:\n{"score_percent": 40, "result": "fail", "reason": "Made up a policy."}\nThanks!'
    verdict = parse_judge_response(raw)
    assert verdict.score_percent == 40
    assert verdict.result == "fail"


def test_parse_judge_response_clamps_score_above_100():
    raw = '{"score_percent": 150, "result": "pass", "reason": "ok"}'
    verdict = parse_judge_response(raw)
    assert verdict.score_percent == 100


def test_parse_judge_response_clamps_negative_score():
    raw = '{"score_percent": -20, "result": "fail", "reason": "bad"}'
    verdict = parse_judge_response(raw)
    assert verdict.score_percent == 0


def test_parse_judge_response_normalizes_nonstandard_result_label_from_score():
    raw = '{"score_percent": 85, "result": "PASSED", "reason": "close enough"}'
    verdict = parse_judge_response(raw)
    assert verdict.result == "pass"


def test_parse_judge_response_low_score_with_bad_label_falls_back_to_fail():
    raw = '{"score_percent": 10, "result": "nope", "reason": "way off"}'
    verdict = parse_judge_response(raw)
    assert verdict.result == "fail"


def test_parse_judge_response_missing_required_fields_raises():
    with pytest.raises(JudgeError):
        parse_judge_response('{"score_percent": 80}')


def test_parse_judge_response_non_numeric_score_raises():
    with pytest.raises(JudgeError):
        parse_judge_response('{"score_percent": "high", "result": "pass", "reason": "x"}')


def test_parse_judge_response_not_json_raises():
    with pytest.raises(JudgeError):
        parse_judge_response("I cannot comply with strict JSON today.")


def test_parse_judge_response_empty_raises():
    with pytest.raises(JudgeError):
        parse_judge_response("")


def test_parse_judge_response_defaults_missing_reason():
    raw = '{"score_percent": 60, "result": "pass"}'
    verdict = parse_judge_response(raw)
    assert verdict.reason


def test_build_judge_messages_marks_criteria_as_semantic_not_literal():
    transcript = [
        {"role": "user", "content": "What's your refund policy?"},
        {"role": "assistant", "content": "I don't have the exact policy, let me check for you."},
    ]
    messages = build_judge_messages("Should not invent a policy", transcript)
    assert messages[0]["role"] == "system"
    system_lower = messages[0]["content"].lower()
    assert "not a literal script" in system_lower or "not a script" in system_lower
    assert "USER: What's your refund policy?" in messages[1]["content"]
    assert "Should not invent a policy" in messages[1]["content"]


@pytest.mark.asyncio
async def test_call_judge_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"score_percent": 77, "result": "pass", "reason": "fine"}',
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    verdict = await call_judge("some criteria", [{"role": "user", "content": "hi"}])
    assert verdict.result == "pass"
    assert verdict.score_percent == 77


@pytest.mark.asyncio
async def test_call_judge_bad_gateway_shape_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    with pytest.raises(JudgeError):
        await call_judge("criteria", [{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_call_judge_http_error_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    with pytest.raises(JudgeError):
        await call_judge("criteria", [{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_call_judge_unparseable_judge_output_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "no json here"}}]},
        )

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    with pytest.raises(JudgeError):
        await call_judge("criteria", [{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_call_judge_retries_once_on_transport_error_then_succeeds(monkeypatch):
    """A connection-level blip reaching llm-gateway (never got a response
    at all) is exactly the failure a retry is meant to cover."""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectError("transient blip")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": '{"score_percent": 90, "result": "pass", "reason": "ok"}'}}
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)
    monkeypatch.setattr("app.judge.asyncio.sleep", lambda *_a, **_kw: _await_none())

    verdict = await call_judge("criteria", [{"role": "user", "content": "hi"}])

    assert attempts["count"] == 2
    assert verdict.result == "pass"


@pytest.mark.asyncio
async def test_call_judge_does_not_retry_on_an_http_status_error(monkeypatch):
    """The critical distinction from the transport-error case above:
    llm-gateway is ITSELF a multi-provider fallback gateway (see
    services/llm-gateway/app/main.py), so a non-2xx response means it
    already tried every configured provider and still failed -- retrying
    that would re-walk the whole already-exhausted chain again, exactly
    the compounding-latency bug fixed in agent-service/app/graph.py. This
    must call the handler exactly once, not twice."""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(502, json={"detail": "All providers failed"})

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    with pytest.raises(JudgeError):
        await call_judge("criteria", [{"role": "user", "content": "hi"}])

    assert attempts["count"] == 1


async def _await_none() -> None:
    return None
