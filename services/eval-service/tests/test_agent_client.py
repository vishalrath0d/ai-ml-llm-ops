"""Tests for the agent-service HTTP client, mocked with httpx.MockTransport.

No real agent-service is required or contacted.
"""
import httpx
import pytest

from app.agent_client import AgentServiceError, run_conversation

_RealAsyncClient = httpx.AsyncClient


async def _no_sleep(*_args, **_kwargs) -> None:
    """Replacement for asyncio.sleep in retry tests -- skips the real
    backoff delay so tests stay fast."""
    return None


def _patch_async_client(monkeypatch, transport: httpx.MockTransport) -> None:
    """Points app.agent_client's httpx.AsyncClient at a mocked transport.

    Uses the real AsyncClient class captured above (not a fresh `httpx.AsyncClient`
    lookup) because monkeypatch.setattr("app.agent_client.httpx.AsyncClient", ...)
    patches the actual shared `httpx` module object -- looking it up again inside
    the replacement would just call the replacement itself and recurse.
    """
    monkeypatch.setattr(
        "app.agent_client.httpx.AsyncClient",
        lambda *a, **kw: _RealAsyncClient(*a, transport=transport, **kw),
    )


@pytest.mark.asyncio
async def test_run_conversation_single_turn_default_extracts_response_key(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat"
        return httpx.Response(200, json={"response": "I don't have that info handy."})

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    transcript = await run_conversation("What's your refund policy?", turns=1)
    assert transcript == [
        {"role": "user", "content": "What's your refund policy?"},
        {"role": "assistant", "content": "I don't have that info handy."},
    ]


@pytest.mark.asyncio
async def test_run_conversation_multi_turn_uses_followups(monkeypatch):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"reply": f"reply-{call_count['n']}"})

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    transcript = await run_conversation("Hello!", turns=2)
    assert len(transcript) == 4
    assert transcript[0] == {"role": "user", "content": "Hello!"}
    assert transcript[1] == {"role": "assistant", "content": "reply-1"}
    assert transcript[2]["role"] == "user"
    assert transcript[3] == {"role": "assistant", "content": "reply-2"}
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_run_conversation_accepts_plain_text_body(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="just plain text, not json")

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    transcript = await run_conversation("Hi", turns=1)
    assert transcript[-1]["content"] == "just plain text, not json"


@pytest.mark.asyncio
async def test_run_conversation_http_error_raises_agent_service_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down for maintenance")

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    with pytest.raises(AgentServiceError):
        await run_conversation("Hi", turns=1)


@pytest.mark.asyncio
async def test_run_conversation_unusable_response_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        # Empty body: resp.json() fails, resp.text is empty -> no reply extractable.
        return httpx.Response(200, content=b"")

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    with pytest.raises(AgentServiceError):
        await run_conversation("Hi", turns=1)


@pytest.mark.asyncio
async def test_run_conversation_succeeds_on_retry_after_one_transient_failure(monkeypatch):
    """A single connection blip talking to agent-service (e.g. mid-run
    under host load) should not fail the whole eval run outright -- this
    is exactly the behavior the retry in app/agent_client.py covers."""
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ConnectError("transient blip")
        return httpx.Response(200, json={"response": "recovered on retry"})

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)
    monkeypatch.setattr("app.agent_client.asyncio.sleep", _no_sleep)

    transcript = await run_conversation("Hi", turns=1)

    assert attempts["count"] == 2
    assert transcript[-1] == {"role": "assistant", "content": "recovered on retry"}


@pytest.mark.asyncio
async def test_run_conversation_raises_after_two_consecutive_failures(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("still down")

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)
    monkeypatch.setattr("app.agent_client.asyncio.sleep", _no_sleep)

    with pytest.raises(AgentServiceError):
        await run_conversation("Hi", turns=1)
