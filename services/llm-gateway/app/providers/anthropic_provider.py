"""Anthropic provider adapter -- uses the official `anthropic` Python SDK.

Anthropic's Messages API takes `system` as a separate top-level parameter
rather than a message with role "system", and requires `max_tokens`
explicitly (no server-side default) -- both are handled here so callers can
keep sending OpenAI-shaped `messages` arrays.
"""
from __future__ import annotations

import json
from typing import AsyncIterator, List, Optional, Tuple

from anthropic import AsyncAnthropic

from app.config import settings
from app.providers.base import BaseLLMProvider, ChatResult, StreamChunk


def _split_system(messages: List[dict]) -> Tuple[Optional[str], List[dict]]:
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    convo = [_to_anthropic_message(m) for m in non_system]
    system = "\n".join(system_parts) if system_parts else None
    return system, convo


def _to_anthropic_message(m: dict) -> dict:
    """Translate one OpenAI-wire message into Anthropic's message shape.

    Anthropic has no `role: "tool"` and no `tool_calls` field on assistant
    messages -- both become typed content blocks instead: an assistant tool
    call becomes a `tool_use` block, and a tool's result becomes a
    `tool_result` block inside a *user* turn (Anthropic requires tool
    results to be user-role content, not their own role).
    """
    if m.get("role") == "tool":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": m.get("content") or "",
                }
            ],
        }
    tool_calls = m.get("tool_calls")
    if tool_calls:
        blocks = []
        if m.get("content"):
            blocks.append({"type": "text", "text": m["content"]})
        for tc in tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments")
            try:
                parsed_args = json.loads(args) if isinstance(args, str) else (args or {})
            except ValueError:
                parsed_args = {}
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": parsed_args,
                }
            )
        return {"role": "assistant", "content": blocks}
    return {"role": m.get("role", "user"), "content": m.get("content") or ""}


def _to_anthropic_tools(tools: Optional[List[dict]]) -> Optional[List[dict]]:
    """Translate OpenAI-format tool schemas into Anthropic's shape --
    Anthropic uses `input_schema` where OpenAI nests the same JSON schema
    under `function.parameters`."""
    if not tools:
        return None
    converted = []
    for t in tools:
        fn = t.get("function", t)
        converted.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return converted


def _extract_anthropic_tool_calls(content_blocks) -> Optional[List[dict]]:
    """Translate Anthropic's `tool_use` content blocks back into the OpenAI
    wire `tool_calls` shape every caller in this project expects."""
    calls = [
        {
            "id": block.id,
            "type": "function",
            "function": {"name": block.name, "arguments": json.dumps(block.input)},
        }
        for block in content_blocks
        if getattr(block, "type", None) == "tool_use"
    ]
    return calls or None


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.model = model or settings.ANTHROPIC_MODEL
        # Same "construct unconditionally, fail at call time" posture as
        # OpenAIProvider (see its docstring) -- a bare `None` here made
        # AsyncAnthropic's own constructor raise a raw, confusing
        # `TypeError` immediately (observed directly, running the default
        # 4-provider chain with no ANTHROPIC_API_KEY configured), rather
        # than a clean 401 surfaced from an actual call. A placeholder key
        # defers that failure to generate()/generate_stream(), which is
        # where a misconfigured provider should fail -- not before the
        # fallback provider even gets a chance to run.
        #
        # timeout=... is not decorative either -- see openai_provider.py's
        # identical fix for the real incident (an unbounded/stalled
        # provider call blocking the whole fallback chain) this addresses
        # across every provider, not just this one.
        self._client = AsyncAnthropic(
            api_key=api_key or settings.ANTHROPIC_API_KEY or "not-configured",
            timeout=settings.HTTP_TIMEOUT_SECONDS,
        )

    async def generate(
        self,
        messages: List[dict],
        temperature: float = 1.0,
        model: Optional[str] = None,
        tools: Optional[List[dict]] = None,
    ) -> ChatResult:
        system, convo = _split_system(messages)
        kwargs = dict(
            model=model or self.model,
            max_tokens=settings.ANTHROPIC_MAX_TOKENS,
            messages=convo,
            temperature=temperature,
        )
        if system:
            kwargs["system"] = system
        anthropic_tools = _to_anthropic_tools(tools)
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        resp = await self._client.messages.create(**kwargs)

        content = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        tool_calls = _extract_anthropic_tool_calls(resp.content)
        usage = resp.usage
        return ChatResult(
            content=content,
            prompt_tokens=usage.input_tokens if usage else 0,
            completion_tokens=usage.output_tokens if usage else 0,
            finish_reason="tool_calls" if tool_calls else (resp.stop_reason or "stop"),
            tool_calls=tool_calls,
        )

    async def generate_stream(
        self,
        messages: List[dict],
        temperature: float = 1.0,
        model: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        system, convo = _split_system(messages)
        kwargs = dict(
            model=model or self.model,
            max_tokens=settings.ANTHROPIC_MAX_TOKENS,
            messages=convo,
            temperature=temperature,
        )
        if system:
            kwargs["system"] = system

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                if text:
                    yield StreamChunk(delta=text)
            final = await stream.get_final_message()
            usage = final.usage
            yield StreamChunk(
                delta="",
                finish_reason=final.stop_reason or "stop",
                prompt_tokens=usage.input_tokens if usage else 0,
                completion_tokens=usage.output_tokens if usage else 0,
            )
