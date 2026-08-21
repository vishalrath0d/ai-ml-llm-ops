"""OpenAI provider adapter -- uses the official `openai` Python SDK."""
from __future__ import annotations

from typing import AsyncIterator, List, Optional

from openai import AsyncOpenAI

from app.config import settings
from app.providers.base import BaseLLMProvider, ChatResult, StreamChunk


class OpenAIProvider(BaseLLMProvider):
    name = "openai"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.model = model or settings.OPENAI_MODEL
        # A missing key must not raise HERE -- the openai SDK (>=1.x) raises
        # eagerly in AsyncOpenAI.__init__ if api_key resolves to None, but
        # this constructor runs unconditionally for every provider in the
        # fallback chain regardless of whether OpenAI is actually configured.
        # A placeholder key lets construction succeed either way; the real
        # failure (401, or whatever) still surfaces correctly the moment
        # `.generate()` actually tries to call OpenAI with it, which is
        # exactly where a mis-configured provider should fail -- not before
        # the fallback provider even gets a chance to run.
        #
        # timeout=... is not decorative either -- the openai SDK's own
        # default (600s) means an unbounded/stalled call here could block
        # the entire fallback chain from ever reaching the providers behind
        # it for ten minutes. See gemini_provider.py's identical fix for the
        # real incident that surfaced this across every provider, not just
        # this one.
        self._client = AsyncOpenAI(
            api_key=api_key or settings.OPENAI_API_KEY or "not-configured",
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )

    async def generate(
        self,
        messages: List[dict],
        temperature: float = 1.0,
        model: Optional[str] = None,
        tools: Optional[List[dict]] = None,
    ) -> ChatResult:
        kwargs: dict = dict(model=model or self.model, messages=messages, temperature=temperature)
        if tools:
            kwargs["tools"] = tools
        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        usage = resp.usage
        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in choice.message.tool_calls
            ]
        return ChatResult(
            content=choice.message.content or "",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            finish_reason=choice.finish_reason or "stop",
            tool_calls=tool_calls,
        )

    async def generate_stream(
        self,
        messages: List[dict],
        temperature: float = 1.0,
        model: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        stream = await self._client.chat.completions.create(
            model=model or self.model,
            messages=messages,
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
        )
        prompt_tokens = 0
        completion_tokens = 0
        finish_reason = "stop"
        async for event in stream:
            if getattr(event, "usage", None):
                prompt_tokens = event.usage.prompt_tokens or 0
                completion_tokens = event.usage.completion_tokens or 0
            if not event.choices:
                continue
            choice = event.choices[0]
            delta_content = choice.delta.content or ""
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            if delta_content:
                yield StreamChunk(delta=delta_content)
        yield StreamChunk(
            delta="",
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
