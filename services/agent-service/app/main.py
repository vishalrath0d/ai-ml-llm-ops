"""FastAPI entrypoint for agent-service.

Exposes:
  * POST /chat     - run the LangGraph agent for one conversational turn
  * GET  /health   - liveness probe
  * GET  /metrics  - Prometheus metrics
  * /mcp           - MCP server (SSE transport), mounted from app/mcp_server.py
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import BaseMessage
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

from app.auth import ApiKeyMiddleware
from app.config import CORS_ALLOWED_ORIGINS, ENVIRONMENT, SERVICE_NAME
from app.graph import run_agent_turn
from app.logging_setup import configure_logging
from app.mcp_server import mcp_asgi_app
from app.metrics import (
    CHAT_LATENCY_SECONDS,
    CHAT_REQUESTS_TOTAL,
    TOOL_CALLS_TOTAL,
    URGENCY_CLASSIFICATIONS_TOTAL,
)
from app.model_registry import classify_urgency, current_status, force_reload
from app.online_eval import is_sampled, run_online_eval
from app.tracing import traced_chat_turn

# A message classified "urgent" by the live MLflow-loaded model gets this
# one-off system note injected for the turn (see app/graph.py) -- a
# concrete, visible behavior change driven by the registry, not just a
# response field nobody reads.
URGENCY_SYSTEM_HINT = (
    "Note: this message was flagged as urgent by an automated classifier. "
    "Lead with empathy, acknowledge the urgency explicitly, and proactively "
    "mention escalation/priority handling if you cannot resolve it yourself."
)

configure_logging(ENVIRONMENT)
logger = logging.getLogger("agent-service")

app = FastAPI(title="agent-service", version="0.1.0")

# CORS_ALLOWED_ORIGINS defaults to "*" only in dev (see app/config.py) --
# outside dev it defaults to allowing nothing until set explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added AFTER CORSMiddleware above -- Starlette's last-added middleware runs
# FIRST on an incoming request, so this ordering makes CORS's own preflight
# handling run before auth ever sees the request (belt-and-suspenders on
# top of ApiKeyMiddleware's own explicit OPTIONS exemption).
app.add_middleware(ApiKeyMiddleware)

# Mount the MCP server (SSE transport) at /mcp. See app/mcp_server.py for the
# tool definitions and README.md for how this fits a common shared-MCP-
# server pattern.
app.mount("/mcp", mcp_asgi_app)

# In-memory per-session conversation history. NOT durable: a restart of this
# process loses every in-flight conversation. A real deployment of this
# pattern would back this with Redis (fast, TTL'd) or Mongo (durable), the
# same way a lot of production conversational-AI session stores work. Swapping this
# dict for a Redis-backed store would only touch _SESSIONS access below.
_SESSIONS: dict[str, list[BaseMessage]] = {}


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Conversation/session identifier.")
    message: str = Field(..., description="The user's message for this turn.")
    customer_id: str | None = Field(
        default=None,
        description=(
            "Optional known customer identity (e.g. from an authenticated session -- "
            "mirrors how a real chat widget already knows who's logged in, rather than "
            "parsing an ID out of free text the way the crm_lookup tool does). When "
            "given, the urgency classifier looks up this customer's live engagement "
            "features from Feast (see app/feature_store_client.py) and factors them "
            "into the urgency label alongside the message text."
        ),
    )


class ChatResponse(BaseModel):
    response: str
    tool_calls: list[str]
    session_id: str
    urgency: str = "unknown"
    urgency_model_version: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/model-status")
def model_status() -> dict[str, Any]:
    """What urgency-classifier version is currently loaded, without forcing
    a reload — see app/model_registry.py."""
    return current_status()


@app.post("/admin/reload-model")
def reload_model() -> dict[str, Any]:
    """Force an immediate reload of the urgency classifier from MLflow's
    `champion` alias, bypassing the normal refresh-interval cache. Use this
    right after flipping an alias in services/mlflow/train_and_log.py so a
    demo doesn't have to wait for the next natural refresh window."""
    return force_reload()


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, background_tasks: BackgroundTasks) -> ChatResponse:
    start = time.perf_counter()
    history = _SESSIONS.get(request.session_id, [])

    # Live MLflow model registry consultation -- see app/model_registry.py.
    # Whatever version is currently tagged `champion` for
    # support-urgency-classifier decides this label; promoting/rolling back
    # that alias changes this on the next request, with zero code change.
    # When customer_id is given, this also pulls that customer's live
    # features from Feast (see app/feature_store_client.py) into the
    # classification -- same text, different customer, can mean a different
    # urgency label.
    urgency, urgency_model_version = classify_urgency(request.message, request.customer_id)
    URGENCY_CLASSIFICATIONS_TOTAL.labels(label=urgency).inc()
    extra_note = URGENCY_SYSTEM_HINT if urgency == "urgent" else None

    with traced_chat_turn(request.session_id, request.message) as trace:
        # `.trace_id` (not `.id`, which is this SPAN's own id) -- the online
        # eval background task below needs the real trace id to attach a
        # score to the right trace after this request has already returned.
        trace_id = getattr(trace, "trace_id", None) or str(uuid.uuid4())
        try:
            response_text, tool_calls, updated_history = run_agent_turn(
                session_id=request.session_id,
                history=history,
                user_message=request.message,
                extra_system_note=extra_note,
            )
        except Exception:
            CHAT_REQUESTS_TOTAL.labels(status="error").inc()
            logger.exception("chat turn failed for session_id=%s", request.session_id)
            raise
        finally:
            CHAT_LATENCY_SECONDS.observe(time.perf_counter() - start)

        _SESSIONS[request.session_id] = updated_history
        for tool_name in tool_calls:
            TOOL_CALLS_TOTAL.labels(tool_name=tool_name).inc()
        CHAT_REQUESTS_TOTAL.labels(status="ok").inc()

        trace.update(
            output={"response": response_text, "tool_calls": tool_calls, "urgency": urgency},
            metadata={"trace_id": trace_id, "urgency_model_version": urgency_model_version},
        )

    # Online eval: sample a fraction of REAL /chat traffic and judge it after
    # the fact -- see app/online_eval.py for why this is a different thing
    # from eval-service. Scheduled via BackgroundTasks so it runs only after
    # this response has already been sent; never adds latency to the caller.
    if is_sampled():
        background_tasks.add_task(run_online_eval, trace_id, request.message, response_text)

    return ChatResponse(
        response=response_text,
        tool_calls=tool_calls,
        session_id=request.session_id,
        urgency=urgency,
        urgency_model_version=urgency_model_version,
    )
