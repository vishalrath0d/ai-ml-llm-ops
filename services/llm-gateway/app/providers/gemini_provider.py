"""Gemini provider adapter -- uses Google's official `google-genai` SDK.

Three translation differences from the OpenAI wire format that matter here,
same spirit as the Anthropic/Ollama adapters:

- Gemini has no "system" role in `contents` -- a system message becomes
  `GenerateContentConfig(system_instruction=...)` instead, same idea as
  Anthropic's separate `system` parameter.
- Gemini's assistant role is called "model", not "assistant".
- Gemini's tool-calling shape has no OpenAI-style `tool_call_id`/`name`
  pairing on the result message -- a function response part only carries the
  function *name*, not the id that requested it. This adapter tracks
  tool_call_id -> function name across the message list (built once per
  request) so an incoming OpenAI-wire "tool" message can be translated back
  into the name Gemini actually needs.
"""
from __future__ import annotations

import base64
import json
import uuid
from typing import AsyncIterator, List, Optional, Tuple

from google import genai
from google.genai import types

from app.config import settings
from app.providers.base import BaseLLMProvider, ChatResult, StreamChunk


def _tool_call_id_to_name(messages: List[dict]) -> dict:
    """Scan every assistant message's tool_calls once, building
    tool_call_id -> function name -- needed because a later "tool" role
    message only carries the id, and Gemini's function_response part needs
    the name, not the id."""
    mapping: dict[str, str] = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {})
            call_id = tc.get("id")
            if call_id and fn.get("name"):
                mapping[call_id] = fn["name"]
    return mapping


def _to_gemini_content(m: dict, id_to_name: dict) -> Optional[types.Content]:
    role = m.get("role")
    if role == "system":
        return None  # handled separately, via system_instruction
    if role == "tool":
        name = id_to_name.get(m.get("tool_call_id", ""), "unknown_tool")
        return types.Content(
            role="user",
            parts=[types.Part.from_function_response(name=name, response={"result": m.get("content") or ""})],
        )
    tool_calls = m.get("tool_calls")
    if tool_calls:
        parts = []
        if m.get("content"):
            parts.append(types.Part.from_text(text=m["content"]))
        for tc in tool_calls:
            fn = tc.get("function", {})
            args = fn.get("arguments")
            try:
                parsed_args = json.loads(args) if isinstance(args, str) else (args or {})
            except ValueError:
                parsed_args = {}
            part = types.Part.from_function_call(name=fn.get("name", ""), args=parsed_args)
            # Gemini's "thinking" models (this project observed it with a
            # gemini-2.5/3.x flash model) attach an opaque thought_signature
            # to a function-call part, and reject a later turn that replays
            # that call without it ("Function call is missing a
            # thought_signature in functionCall parts", 400
            # INVALID_ARGUMENT) -- real, observed incident, not a load issue.
            # The OpenAI-wire tool_calls shape this round-trips through
            # (llm-gateway -> agent-service's langchain/openai client -> back
            # to llm-gateway on the next turn) has no field for it, so it
            # rides inside `id` the same way `_tool_call_id_to_name` already
            # treats `id` as this adapter's own opaque channel -- see
            # _extract_tool_calls below for the encoding side.
            _, _, sig_b64 = tc.get("id", "").partition("::")
            if sig_b64:
                part.thought_signature = base64.b64decode(sig_b64)
            parts.append(part)
        return types.Content(role="model", parts=parts)
    gemini_role = "model" if role == "assistant" else "user"
    return types.Content(role=gemini_role, parts=[types.Part.from_text(text=m.get("content") or "")])


def _split_system(messages: List[dict]) -> Tuple[Optional[str], List[types.Content]]:
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    id_to_name = _tool_call_id_to_name(messages)
    contents = [c for m in messages if (c := _to_gemini_content(m, id_to_name)) is not None]
    system = "\n".join(system_parts) if system_parts else None
    return system, contents


def _to_gemini_tools(tools: Optional[List[dict]]) -> Optional[List[types.Tool]]:
    """Translate OpenAI-format tool schemas into Gemini's function-declaration
    shape. Gemini's `parameters` field is an OpenAPI Schema object, which is
    close enough to the plain JSON schema OpenAI uses for this project's own
    flat tool schemas (crm_lookup, search_knowledge_base) to pass through
    directly, same best-effort approach the Anthropic adapter takes."""
    if not tools:
        return None
    declarations = []
    for t in tools:
        fn = t.get("function", t)
        declarations.append(
            types.FunctionDeclaration(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=fn.get("parameters") or {"type": "object", "properties": {}},
            )
        )
    return [types.Tool(function_declarations=declarations)]


def _extract_tool_calls(candidate) -> Optional[List[dict]]:
    if not candidate or not candidate.content or not candidate.content.parts:
        return None
    calls = []
    for part in candidate.content.parts:
        fc = getattr(part, "function_call", None)
        if fc is not None:
            call_id = f"call_{uuid.uuid4().hex[:12]}"
            sig = getattr(part, "thought_signature", None)
            if sig:
                # Smuggle the signature through `id` -- see _to_gemini_content's
                # matching comment for why: the OpenAI-wire tool_calls shape has
                # nowhere else to carry it through agent-service's
                # langchain/openai round-trip, but `id` is treated as opaque by
                # every hop and is echoed back verbatim on the "tool" result
                # message, so it survives intact.
                call_id = f"{call_id}::{base64.b64encode(sig).decode('ascii')}"
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": fc.name, "arguments": json.dumps(dict(fc.args or {}))},
                }
            )
    return calls or None


def _extract_text(candidate) -> str:
    if not candidate or not candidate.content or not candidate.content.parts:
        return ""
    return "".join(part.text for part in candidate.content.parts if getattr(part, "text", None))


class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.model = model or settings.GEMINI_MODEL
        # Same "construct unconditionally, fail at call time" posture as
        # OpenAIProvider -- a missing key must not raise here, or a
        # misconfigured Gemini slot would break the fallback chain for every
        # OTHER provider behind it too, not just itself.
        #
        # http_options=HttpOptions(timeout=...) is not decorative -- without
        # it, the google-genai SDK has no bound on this call at all, and a
        # real incident during this project's own use showed exactly why
        # that matters: a stalled/slow network path to Gemini caused a
        # SINGLE provider call to hang far longer than any client-side
        # caller was willing to wait, and because llm-gateway's fallback
        # chain only moves to the next provider once the current call
        # actually RETURNS (success or exception), an unbounded hang here
        # defeats the entire point of having OpenAI/Anthropic/Ollama as
        # fallbacks -- they never get a chance to run. HttpOptions.timeout
        # is in MILLISECONDS (confirmed against the installed SDK's own
        # field description), not seconds -- easy to get wrong by 1000x.
        self._client = genai.Client(
            api_key=api_key or settings.GEMINI_API_KEY or "not-configured",
            http_options=types.HttpOptions(timeout=int(settings.HTTP_TIMEOUT_SECONDS * 1000)),
        )

    async def generate(
        self,
        messages: List[dict],
        temperature: float = 1.0,
        model: Optional[str] = None,
        tools: Optional[List[dict]] = None,
    ) -> ChatResult:
        system, contents = _split_system(messages)
        config_kwargs: dict = {"temperature": temperature}
        if system:
            config_kwargs["system_instruction"] = system
        gemini_tools = _to_gemini_tools(tools)
        if gemini_tools:
            config_kwargs["tools"] = gemini_tools

        resp = await self._client.aio.models.generate_content(
            model=model or self.model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )

        candidate = resp.candidates[0] if resp.candidates else None
        tool_calls = _extract_tool_calls(candidate)
        usage = resp.usage_metadata
        return ChatResult(
            content=_extract_text(candidate),
            prompt_tokens=usage.prompt_token_count if usage else 0,
            completion_tokens=usage.candidates_token_count if usage else 0,
            finish_reason="tool_calls" if tool_calls else "stop",
            tool_calls=tool_calls,
        )

    async def generate_stream(
        self,
        messages: List[dict],
        temperature: float = 1.0,
        model: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        system, contents = _split_system(messages)
        config_kwargs: dict = {"temperature": temperature}
        if system:
            config_kwargs["system_instruction"] = system

        prompt_tokens = 0
        completion_tokens = 0
        stream = await self._client.aio.models.generate_content_stream(
            model=model or self.model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        async for chunk in stream:
            if chunk.usage_metadata:
                prompt_tokens = chunk.usage_metadata.prompt_token_count or 0
                completion_tokens = chunk.usage_metadata.candidates_token_count or 0
            text = chunk.text or ""
            if text:
                yield StreamChunk(delta=text)
        yield StreamChunk(
            delta="",
            finish_reason="stop",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
