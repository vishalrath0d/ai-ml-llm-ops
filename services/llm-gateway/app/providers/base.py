"""Common interface every provider adapter (ollama / openai / anthropic)
implements, so the gateway can call `provider.generate(...)` /
`provider.generate_stream(...)` without caring which backend it's talking to."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, List, Optional


@dataclass
class ChatResult:
    """Result of a non-streaming chat completion call."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str = "stop"
    # OpenAI wire format: [{"id": ..., "type": "function", "function": {"name": ..., "arguments": "<json string>"}}]
    tool_calls: Optional[List[dict]] = None


@dataclass
class StreamChunk:
    """One chunk of a streaming chat completion.

    `delta` is the incremental text for this chunk (may be empty on the
    final chunk). `finish_reason` / `prompt_tokens` / `completion_tokens`
    are only populated on the last chunk a provider yields, once it knows
    the final answer -- callers should keep the most recent non-None value
    of each rather than expecting them on every chunk.
    """

    delta: str
    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


class BaseLLMProvider(ABC):
    """Common interface for a single LLM backend."""

    name: str = "base"

    @abstractmethod
    async def generate(
        self,
        messages: List[dict],
        temperature: float = 1.0,
        model: Optional[str] = None,
        tools: Optional[List[dict]] = None,
    ) -> ChatResult:
        """Non-streaming chat completion. `messages` is a list of
        OpenAI-wire-format dicts (`role`/`content`, plus optionally
        `tool_calls`/`tool_call_id`/`name` for a tool-calling turn). `tools`
        is the OpenAI-format tool/function schema list, or None. Each
        provider is responsible for translating both into whatever shape its
        own backend actually expects, and translating any tool call it gets
        back into this same OpenAI wire shape on the way out (see
        `ChatResult.tool_calls`)."""
        raise NotImplementedError

    @abstractmethod
    def generate_stream(
        self,
        messages: List[dict],
        temperature: float = 1.0,
        model: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """Streaming chat completion. Implementations are async generators
        yielding `StreamChunk`s."""
        raise NotImplementedError
