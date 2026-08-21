"""Ollama provider adapter -- fully offline/free, calls OLLAMA_BASE_URL's
/api/chat endpoint. This is the default provider so the gateway (and every
service that calls it) works out of the box with no API keys."""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, List, Optional

import httpx

from app.config import settings
from app.providers.base import BaseLLMProvider, ChatResult, StreamChunk


def _prepare_messages_for_ollama(messages: List[dict]) -> List[dict]:
    """Translate OpenAI-wire messages into what Ollama's /api/chat expects.

    Two differences that matter for multi-turn tool calling to actually work:
    - Ollama's Go server unmarshals `tool_calls[].function.arguments` into a
      map, so a JSON-*string* arguments field (the OpenAI wire format
      langchain_openai produces when it replays a prior AIMessage back to
      us) has to be parsed into a dict before Ollama will accept it.
    - `content` must be a string, never null (an assistant message that only
      carries tool_calls has `content: None` in OpenAI wire format).
    """
    prepared = []
    for m in messages:
        m = dict(m)
        if m.get("content") is None:
            m["content"] = ""
        tool_calls = m.get("tool_calls")
        if tool_calls:
            new_calls = []
            for tc in tool_calls:
                fn = dict(tc.get("function", {}))
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        fn["arguments"] = json.loads(args)
                    except ValueError:
                        pass
                new_calls.append({**tc, "function": fn})
            m["tool_calls"] = new_calls
        prepared.append(m)
    return prepared


def _normalize_ollama_tool_calls(raw: Optional[List[dict]]) -> Optional[List[dict]]:
    """Translate Ollama's native tool_calls shape into the OpenAI wire shape
    every caller in this project (langchain_openai's ChatOpenAI) expects --
    notably, `arguments` as a JSON-encoded string, not a parsed dict."""
    if not raw:
        return None
    normalized = []
    for call in raw:
        fn = call.get("function") or {}
        args = fn.get("arguments")
        args_str = args if isinstance(args, str) else json.dumps(args or {})
        normalized.append(
            {
                "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {"name": fn.get("name", ""), "arguments": args_str},
            }
        )
    return normalized


class OllamaProvider(BaseLLMProvider):
    name = "ollama"

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None) -> None:
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or settings.OLLAMA_MODEL

    async def generate(
        self,
        messages: List[dict],
        temperature: float = 1.0,
        model: Optional[str] = None,
        tools: Optional[List[dict]] = None,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": _prepare_messages_for_ollama(messages),
            "stream": False,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = tools
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()

        message = data.get("message") or {}
        content = message.get("content", "")
        # Ollama reports actual token counts for both prompt and completion --
        # no need for a heuristic estimate here.
        prompt_tokens = data.get("prompt_eval_count") or 0
        completion_tokens = data.get("eval_count") or 0
        tool_calls = _normalize_ollama_tool_calls(message.get("tool_calls"))
        return ChatResult(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason="tool_calls" if tool_calls else "stop",
            tool_calls=tool_calls,
        )

    async def generate_stream(
        self,
        messages: List[dict],
        temperature: float = 1.0,
        model: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature},
        }
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS) as client:
            async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    msg = obj.get("message") or {}
                    delta = msg.get("content", "")
                    if obj.get("done"):
                        yield StreamChunk(
                            delta=delta,
                            finish_reason="stop",
                            prompt_tokens=obj.get("prompt_eval_count") or 0,
                            completion_tokens=obj.get("eval_count") or 0,
                        )
                    elif delta:
                        yield StreamChunk(delta=delta)
