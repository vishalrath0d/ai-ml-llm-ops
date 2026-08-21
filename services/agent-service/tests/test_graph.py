"""Tests for the LangGraph agent's routing/loop-guard logic (app/graph.py).

These exercise the actual compiled graph end-to-end (router -> tool ->
router -> respond cycles), with the LLM call replaced by a scripted
FakeLLM (see conftest.py) so no live llm-gateway is required. The tool
nodes themselves run for real against the in-memory mock CRM, so a passing
test proves the whole router/tool-dispatch/loop-guard wiring, not just the
pure routing function in isolation.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage

from app.config import MAX_ITERATIONS
from app.graph import route_after_router, run_agent_turn


def _ai_tool_call(tool_name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": tool_name, "args": args, "id": call_id}])


def _ai_final(content: str) -> AIMessage:
    return AIMessage(content=content)


# ---------------------------------------------------------------------------
# Unit tests for the pure routing/loop-guard decision function
# ---------------------------------------------------------------------------


def test_route_dispatches_to_search_knowledge_base():
    state = {
        "iterations": 1,
        "messages": [_ai_tool_call("search_knowledge_base", {"query": "pricing"})],
    }
    assert route_after_router(state) == "search_knowledge_base"


def test_route_dispatches_to_crm_lookup():
    state = {
        "iterations": 1,
        "messages": [_ai_tool_call("crm_lookup", {"identifier": "cust_001"})],
    }
    assert route_after_router(state) == "crm_lookup"


def test_route_responds_when_no_tool_call():
    state = {"iterations": 1, "messages": [_ai_final("Here is your answer.")]}
    assert route_after_router(state) == "respond"


def test_route_falls_back_to_respond_for_unknown_tool():
    state = {
        "iterations": 1,
        "messages": [_ai_tool_call("some_unregistered_tool", {})],
    }
    assert route_after_router(state) == "respond"


def test_route_loop_guard_forces_respond_at_max_iterations():
    # Even though the model still wants a tool, the loop guard wins.
    state = {
        "iterations": MAX_ITERATIONS,
        "messages": [_ai_tool_call("crm_lookup", {"identifier": "cust_001"})],
    }
    assert route_after_router(state) == "respond"


def test_route_loop_guard_allows_calls_below_the_limit():
    state = {
        "iterations": MAX_ITERATIONS - 1,
        "messages": [_ai_tool_call("crm_lookup", {"identifier": "cust_001"})],
    }
    assert route_after_router(state) == "crm_lookup"


# ---------------------------------------------------------------------------
# End-to-end graph runs (router -> tool -> router -> respond)
# ---------------------------------------------------------------------------


def test_direct_response_without_any_tool_call(patch_build_llm):
    """router decides no tool is needed -> respond immediately."""
    patch_build_llm(tooled=[_ai_final("Hi! How can I help you today?")])

    response, tool_calls, history = run_agent_turn("session-1", [], "hello")

    assert response == "Hi! How can I help you today?"
    assert tool_calls == []
    # system + human + final AI = 3 messages for a brand-new session
    assert len(history) == 3


def test_single_crm_lookup_round_trip(patch_build_llm):
    """router calls crm_lookup, sees the (real, mocked-CRM) result, then answers."""
    fakes = patch_build_llm(
        tooled=[
            _ai_tool_call("crm_lookup", {"identifier": "cust_001"}),
            _ai_final("Jane Doe is on the Pro plan."),
        ]
    )

    response, tool_calls, history = run_agent_turn(
        "session-2", [], "What plan is cust_001 on?"
    )

    assert response == "Jane Doe is on the Pro plan."
    assert tool_calls == ["crm_lookup"]
    assert fakes["tooled"].call_count == 2  # router ran twice: before and after the tool

    # The tool actually ran against the real mock CRM data (not just faked).
    tool_messages = [m for m in history if type(m).__name__ == "ToolMessage"]
    assert len(tool_messages) == 1
    assert "Jane Doe" in tool_messages[0].content
    assert "Pro" in tool_messages[0].content


def test_search_knowledge_base_round_trip(patch_build_llm, monkeypatch):
    """router calls search_knowledge_base; the rag-service HTTP call is mocked."""
    from unittest.mock import MagicMock

    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"answer": "You can upgrade from Settings > Billing."}
    monkeypatch.setattr("app.tools_impl.httpx.post", lambda *a, **k: fake_response)

    patch_build_llm(
        tooled=[
            _ai_tool_call("search_knowledge_base", {"query": "how do I upgrade my plan?"}),
            _ai_final("Go to Settings > Billing to upgrade."),
        ]
    )

    response, tool_calls, history = run_agent_turn(
        "session-3", [], "how do I upgrade my plan?"
    )

    assert response == "Go to Settings > Billing to upgrade."
    assert tool_calls == ["search_knowledge_base"]


def test_multi_tool_conversation_preserves_order(patch_build_llm, monkeypatch):
    """The model calls crm_lookup, then (separately) search_knowledge_base,
    before finally responding - tool_calls should reflect that order."""
    fakes = patch_build_llm(
        tooled=[
            _ai_tool_call("crm_lookup", {"identifier": "cust_003"}, call_id="call_1"),
            _ai_tool_call("search_knowledge_base", {"query": "past due account"}, call_id="call_2"),
            _ai_final("Your account is past due; here's how to fix that: ..."),
        ]
    )
    monkeypatch.setattr(
        "app.graph.search_knowledge_base_impl",
        lambda query: "Pay your balance to reactivate.",
    )

    response, tool_calls, history = run_agent_turn("session-4", [], "why is my account restricted?")

    assert tool_calls == ["crm_lookup", "search_knowledge_base"]
    assert fakes["tooled"].call_count == 3


def test_loop_guard_stops_infinite_tool_ping_pong(patch_build_llm):
    """If the model NEVER stops requesting tools, the loop guard must still
    terminate the turn within MAX_ITERATIONS router visits, and respond_node
    falls back to one untooled LLM call to produce a real answer."""
    # The model asks for crm_lookup every single time it's given a chance.
    always_wants_a_tool = [
        _ai_tool_call("crm_lookup", {"identifier": "cust_001"}, call_id=f"call_{i}")
        for i in range(MAX_ITERATIONS + 2)
    ]
    fakes = patch_build_llm(
        tooled=always_wants_a_tool,
        fallback=[_ai_final("Best-effort answer without further tool calls.")],
    )

    response, tool_calls, history = run_agent_turn("session-5", [], "loop me forever")

    assert response == "Best-effort answer without further tool calls."
    # router should have been invoked exactly MAX_ITERATIONS times before the
    # loop guard trips (route_after_router checks `iterations >= MAX_ITERATIONS`
    # right after the increment on the MAX_ITERATIONS-th call). The tool
    # itself only actually runs MAX_ITERATIONS - 1 times: on the router's
    # MAX_ITERATIONS-th visit, the loop guard routes straight to respond
    # instead of letting one more tool call through.
    assert fakes["tooled"].call_count == MAX_ITERATIONS
    assert fakes["fallback"].call_count == 1
    assert len(tool_calls) == MAX_ITERATIONS - 1
