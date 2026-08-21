"""Shared pytest fixtures/helpers for agent-service tests.

No test in this suite makes a real network call: the llm-gateway call is
replaced by monkeypatching `app.graph._build_llm` (see `FakeLLM` /
`patch_build_llm` below), and any RAG/CRM calls hit either the real
in-memory mock CRM or an httpx transport mock.

Why not mock at the HTTP layer for the LLM call? `langchain_openai` goes
through the `openai` SDK's own HTTP client, whose exact transport plumbing
is an implementation detail we don't want these tests coupled to. Patching
the one seam this service itself defines (`_build_llm`) is more robust and
just as effective at proving "no live gateway is needed".
"""
from __future__ import annotations

from typing import Any, Callable

import pytest
from langchain_core.messages import AIMessage


class FakeLLM:
    """A stand-in for a (possibly tool-bound) ChatOpenAI instance.

    Each call to `.invoke(messages)` pops and returns the next canned
    AIMessage from `responses`, so a test can script an exact sequence of
    "what the LLM said" turns.
    """

    def __init__(self, responses: list[AIMessage]):
        self._responses = list(responses)
        self.call_count = 0

    def invoke(self, messages: list[Any]) -> AIMessage:
        self.call_count += 1
        if not self._responses:
            raise AssertionError(
                f"FakeLLM.invoke() called more times ({self.call_count}) than "
                "responses were scripted for this test."
            )
        return self._responses.pop(0)


@pytest.fixture
def patch_build_llm(monkeypatch: pytest.MonkeyPatch) -> Callable[..., dict[str, FakeLLM]]:
    """Patch app.graph._build_llm to return scripted FakeLLM instances.

    Usage:
        fakes = patch_build_llm(tooled=[ai_msg_1, ai_msg_2], fallback=[ai_msg_3])
        ... run the graph ...
        assert fakes["tooled"].call_count == 2

    `tooled` responses are returned while the graph calls `_build_llm(bind_tools=True)`
    (the router node). `fallback` responses are returned for
    `_build_llm(bind_tools=False)` (the loop-guard's final untooled answer in
    respond_node).
    """

    def _apply(
        tooled: list[AIMessage] | None = None,
        fallback: list[AIMessage] | None = None,
    ) -> dict[str, FakeLLM]:
        tooled_llm = FakeLLM(tooled or [])
        fallback_llm = FakeLLM(fallback or [])

        def _fake_build_llm(bind_tools: bool):
            return tooled_llm if bind_tools else fallback_llm

        import app.graph as graph_module

        monkeypatch.setattr(graph_module, "_build_llm", _fake_build_llm)
        return {"tooled": tooled_llm, "fallback": fallback_llm}

    return _apply
