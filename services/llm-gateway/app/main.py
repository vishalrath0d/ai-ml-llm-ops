"""
llm-gateway -- unified LLM gateway.

Every other service in the hands-on project calls THIS service instead of
calling OpenAI/Anthropic/Ollama directly. See README.md for the full
rationale (it mirrors a common production multi-LLM-fallback pattern and
a litellm-style unified-gateway pattern).

Endpoints:
  POST /v1/chat/completions  -- OpenAI-compatible, streaming or not
  POST /admin/chaos          -- set CHAOS_LATENCY_MS / CHAOS_ERROR_RATE live
  GET  /admin/chaos          -- read current chaos settings
  GET  /health               -- liveness check
  GET  /metrics              -- Prometheus exposition format
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import AsyncIterator, List, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.auth import ApiKeyMiddleware
from app.config import chaos_state, settings
from app.rate_limit import RateLimitMiddleware
from app.guardrails import REDACTION_MESSAGE, REFUSAL_MESSAGE, check_input, check_output
from app.metrics import GUARDRAIL_TRIGGERED, REQUEST_COUNT, REQUEST_LATENCY, TOKEN_USAGE, metrics_response, record_cost
from app.providers.base import ChatResult, StreamChunk
from app.providers.factory import get_provider
from app.schemas import (
    ChaosConfigRequest,
    ChaosConfigResponse,
    ChatCompletionChoice,
    ChatCompletionChoiceMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Usage,
)
from app.logging_setup import configure_logging
from app.tracing import log_error, log_generation, start_trace

configure_logging(settings.ENVIRONMENT)
logger = logging.getLogger("llm_gateway")

app = FastAPI(
    title="llm-gateway",
    description="Unified multi-provider LLM gateway (gemini / openai / anthropic / ollama) with automatic N-deep fallback, token-based routing, chaos injection, Prometheus metrics, and Langfuse tracing.",
    version="1.0.0",
)

# CORS_ALLOWED_ORIGINS defaults to "*" only in dev (see app/config.py) --
# outside dev it defaults to allowing nothing until set explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added AFTER CORSMiddleware -- see agent-service/app/main.py's identical
# comment for why that ordering matters (CORS preflight must run first).
# ApiKeyMiddleware before RateLimitMiddleware: no reason to count an
# unauthenticated, already-rejected request against a client's quota.
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(RateLimitMiddleware)


def _messages_to_dicts(messages: List[ChatMessage]) -> List[dict]:
    dicts = []
    for m in messages:
        d: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            d["tool_calls"] = [tc.model_dump() for tc in m.tool_calls]
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        dicts.append(d)
    return dicts


def _estimate_tokens(messages: List[dict]) -> int:
    """Cheap, dependency-free token estimate (~4 chars/token, the standard
    rule-of-thumb approximation) used purely for chain filtering below --
    not billing. Getting this exactly right doesn't matter here: it only
    needs to be in the right order of magnitude to tell "fits in a 4K-token
    Ollama window" apart from "needs Gemini's 1M-token window", not to match
    a real tokenizer's count. Providers that report real usage (Ollama's
    `prompt_eval_count`, OpenAI/Anthropic/Gemini's own usage fields) already
    do so from their actual response, independent of this estimate."""
    total_chars = sum(len(m.get("content") or "") for m in messages)
    return total_chars // 4


def _provider_chain(estimated_tokens: int = 0) -> List[str]:
    """The ordered list of provider names to try, narrowed by whichever
    providers can actually fit this request:

    - Every provider in LLM_PROVIDER_CHAIN is tried, in order, until one
      succeeds -- this is the N-deep fallback chain (Gemini -> OpenAI ->
      Anthropic -> Ollama by default; see app/config.py).
    - Token-based routing: a provider whose configured context window is
      smaller than this request's estimated size is skipped, instead of
      being tried and guaranteed to fail with a context-length error. If
      literally nothing fits (a pathological request bigger than every
      configured window), the original, unfiltered chain is returned rather
      than an empty one -- a real "context length exceeded" from whichever
      provider actually gets tried is a more useful failure than the
      gateway refusing to even attempt the request.
    """
    chain = list(settings.LLM_PROVIDER_CHAIN)
    if estimated_tokens <= 0:
        return chain
    fitting = [p for p in chain if estimated_tokens <= settings.PROVIDER_CONTEXT_WINDOWS.get(p, float("inf"))]
    return fitting or chain


async def _maybe_inject_chaos(provider_name: str) -> None:
    """Sleep CHAOS_LATENCY_MS (if set) and roll the dice on CHAOS_ERROR_RATE
    (if set), both read from the live, runtime-mutable `chaos_state` -- see
    app/config.py and POST /admin/chaos."""
    if chaos_state.latency_ms > 0:
        await asyncio.sleep(chaos_state.latency_ms / 1000.0)
    if chaos_state.error_rate > 0 and random.random() < chaos_state.error_rate:
        REQUEST_COUNT.labels(provider=provider_name, status="chaos_error").inc()
        raise HTTPException(
            status_code=500,
            detail="Synthetic chaos error injected by /admin/chaos (CHAOS_ERROR_RATE)",
        )


# ---------------------------------------------------------------------------
# Liveness / metrics / admin
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "provider_chain": settings.LLM_PROVIDER_CHAIN}


@app.get("/metrics")
async def metrics() -> Response:
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)


@app.post("/admin/chaos", response_model=ChaosConfigResponse)
async def set_chaos(cfg: ChaosConfigRequest) -> ChaosConfigResponse:
    """Flip chaos-injection knobs at runtime -- see README.md 'Testing the
    chaos endpoints'. Setting env vars at container start can't be changed
    without a restart, which defeats the point of a live hands-on demo."""
    if cfg.latency_ms is not None:
        chaos_state.latency_ms = cfg.latency_ms
    if cfg.error_rate is not None:
        chaos_state.error_rate = cfg.error_rate
    return ChaosConfigResponse(chaos=chaos_state.as_dict())


@app.get("/admin/chaos", response_model=ChaosConfigResponse)
async def get_chaos() -> ChaosConfigResponse:
    return ChaosConfigResponse(chaos=chaos_state.as_dict())


# ---------------------------------------------------------------------------
# Chat completions
# ---------------------------------------------------------------------------


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    messages = _messages_to_dicts(req.messages)
    temperature = req.temperature if req.temperature is not None else 1.0
    chain = _provider_chain(_estimate_tokens(messages))

    trace = start_trace(
        name="chat-completion",
        input_data=messages,
        metadata={
            "requested_model": req.model,
            "provider_chain": chain,
            "stream": bool(req.stream),
        },
    )

    # Inline input guardrail -- runs on EVERY request, before any provider is
    # called, unlike online-eval/judge-based checks elsewhere in this project
    # (see app/guardrails.py's module docstring for why this one is
    # deliberately cheap and blocking rather than an LLM call).
    guard = check_input(messages)
    if guard.triggered:
        GUARDRAIL_TRIGGERED.labels(guardrail=guard.guardrail, direction="input").inc()
        log_error(trace, f"blocked by input guardrail '{guard.guardrail}': {guard.detail}")
        logger.warning("input guardrail '%s' triggered: %s", guard.guardrail, guard.detail)
        return ChatCompletionResponse(
            model=req.model,
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionChoiceMessage(content=REFUSAL_MESSAGE),
                    finish_reason="content_filter",
                )
            ],
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )

    if req.stream:
        return StreamingResponse(
            _stream_chat_completion(req.model, messages, temperature, chain, trace),
            media_type="text/event-stream",
        )

    return await _non_stream_chat_completion(req.model, messages, temperature, chain, trace, req.tools)


async def _non_stream_chat_completion(
    model: str,
    messages: List[dict],
    temperature: float,
    chain: List[str],
    trace,
    tools: Optional[List[dict]] = None,
) -> ChatCompletionResponse:
    last_error: Optional[str] = None

    for provider_name in chain:
        start = time.monotonic()
        try:
            await _maybe_inject_chaos(provider_name)
            provider = get_provider(provider_name)
            result: ChatResult = await provider.generate(messages, temperature=temperature, tools=tools)

            elapsed = time.monotonic() - start
            REQUEST_LATENCY.labels(provider=provider_name).observe(elapsed)
            REQUEST_COUNT.labels(provider=provider_name, status="success").inc()
            TOKEN_USAGE.labels(provider=provider_name, kind="prompt").inc(result.prompt_tokens)
            TOKEN_USAGE.labels(provider=provider_name, kind="completion").inc(result.completion_tokens)
            record_cost(provider_name, result.prompt_tokens, result.completion_tokens)

            log_generation(
                trace,
                name=provider_name,
                model=model,
                input_data=messages,
                output_data=result.content or {"tool_calls": result.tool_calls},
                usage={"input": result.prompt_tokens, "output": result.completion_tokens},
            )

            # Inline output guardrail -- only applies when the model actually
            # produced text (a tool-calling turn's content is empty/None, and
            # PII detection on an empty string is meaningless).
            response_content = result.content
            finish_reason = result.finish_reason
            if response_content:
                out_guard = check_output(response_content)
                if out_guard.triggered:
                    GUARDRAIL_TRIGGERED.labels(guardrail=out_guard.guardrail, direction="output").inc()
                    logger.warning("output guardrail '%s' triggered: %s", out_guard.guardrail, out_guard.detail)
                    response_content = REDACTION_MESSAGE
                    finish_reason = "content_filter"

            return ChatCompletionResponse(
                model=model,
                choices=[
                    ChatCompletionChoice(
                        message=ChatCompletionChoiceMessage(
                            content=response_content or None,
                            tool_calls=result.tool_calls,
                        ),
                        finish_reason=finish_reason,
                    )
                ],
                usage=Usage(
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    total_tokens=result.prompt_tokens + result.completion_tokens,
                ),
            )
        except HTTPException:
            # Chaos-injected errors are intentional test failures, not a
            # reason to fall back -- surface them as-is.
            raise
        except Exception as exc:  # noqa: BLE001 - any provider failure triggers fallback
            elapsed = time.monotonic() - start
            REQUEST_LATENCY.labels(provider=provider_name).observe(elapsed)
            REQUEST_COUNT.labels(provider=provider_name, status="error").inc()
            # str(exc) is empty for several common httpx/asyncio exceptions
            # (e.g. asyncio.TimeoutError, httpx.ConnectError raised with no
            # message) -- always include the exception type name too, or a
            # "Provider 'x' failed: " warning with nothing after the colon
            # is useless for debugging (this happened for real during
            # verification: an empty message on an Ollama timeout under
            # memory pressure).
            error_detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            logger.warning("Provider '%s' failed: %s", provider_name, error_detail)
            last_error = error_detail
            continue

    log_error(trace, str(last_error))
    raise HTTPException(status_code=502, detail=f"All providers failed. Last error: {last_error}")


async def _stream_chat_completion(
    model: str,
    messages: List[dict],
    temperature: float,
    chain: List[str],
    trace,
) -> AsyncIterator[str]:
    accumulated: List[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    finish_reason = "stop"
    used_provider: Optional[str] = None
    last_error: Optional[str] = None
    started = False

    for provider_name in chain:
        start = time.monotonic()
        try:
            await _maybe_inject_chaos(provider_name)
            provider = get_provider(provider_name)
            stream: AsyncIterator[StreamChunk] = provider.generate_stream(messages, temperature=temperature)

            async for chunk in stream:
                started = True
                used_provider = provider_name
                if chunk.delta:
                    accumulated.append(chunk.delta)
                    payload = {
                        "id": "chatcmpl-stream",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {"index": 0, "delta": {"content": chunk.delta}, "finish_reason": None}
                        ],
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                if chunk.prompt_tokens is not None:
                    prompt_tokens = chunk.prompt_tokens
                if chunk.completion_tokens is not None:
                    completion_tokens = chunk.completion_tokens
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason

            elapsed = time.monotonic() - start
            REQUEST_LATENCY.labels(provider=provider_name).observe(elapsed)
            REQUEST_COUNT.labels(provider=provider_name, status="success").inc()
            TOKEN_USAGE.labels(provider=provider_name, kind="prompt").inc(prompt_tokens)
            TOKEN_USAGE.labels(provider=provider_name, kind="completion").inc(completion_tokens)
            record_cost(provider_name, prompt_tokens, completion_tokens)
            last_error = None
            break
        except HTTPException as exc:
            # Chaos-injected error before anything streamed -- try the next
            # provider in the chain (if any) just like the non-streaming path.
            elapsed = time.monotonic() - start
            REQUEST_LATENCY.labels(provider=provider_name).observe(elapsed)
            last_error = str(exc.detail)
            if started:
                break
            continue
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - start
            REQUEST_LATENCY.labels(provider=provider_name).observe(elapsed)
            REQUEST_COUNT.labels(provider=provider_name, status="error").inc()
            # See the non-streaming handler above for why the exception type
            # name is included -- str(exc) alone is empty for several
            # common httpx/asyncio exceptions.
            error_detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            logger.warning("Streaming provider '%s' failed: %s", provider_name, error_detail)
            last_error = error_detail
            if started:
                # Already streamed partial content to the client -- cannot
                # cleanly restart on a different provider mid-stream. Surface
                # an error event and end the stream instead of silently
                # switching backends under the client.
                break
            continue

    if last_error is not None and not started:
        log_error(trace, str(last_error))
        error_payload = {"error": {"message": str(last_error), "type": "provider_error"}}
        yield f"data: {json.dumps(error_payload)}\n\n"
        yield "data: [DONE]\n\n"
        return

    final_content = "".join(accumulated)
    # No output guardrail here, deliberately: by the time `final_content` is
    # assembled every chunk has already been streamed to the caller, so there
    # is nothing left to redact -- unlike the non-streaming path above, which
    # can still swap `response_content` before the single response is sent.
    # A real system wanting output moderation on streamed responses needs to
    # buffer and check per-chunk (or per-sentence) before forwarding each
    # one, trading away time-to-first-token to make redaction possible at
    # all -- a real tradeoff, not something this demo works around.
    log_generation(
        trace,
        name=used_provider or chain[0],
        model=model,
        input_data=messages,
        output_data=final_content,
        usage={"input": prompt_tokens, "output": completion_tokens},
    )

    final_payload = {
        "id": "chatcmpl-stream",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    yield f"data: {json.dumps(final_payload)}\n\n"
    yield "data: [DONE]\n\n"
