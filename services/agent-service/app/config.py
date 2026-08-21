"""Centralized configuration read from environment variables.

Every external dependency of this service is configurable via env var so it
can be swapped between local docker-compose, CI, and (hypothetically) a real
cluster without code changes. All defaults point at the docker-compose
service names used by this hands-on project (see ../compose.fragment.yml and
the top-level docker-compose.yml that stitches all services together).
"""
from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# --- Environment / multi-env posture -------------------------------------
# One of "dev" / "test" / "staging" / "prod". Drives defaults that should
# NOT be the same everywhere (CORS openness, whether an API key is
# required) without needing a different codebase or Dockerfile per
# environment -- only different env vars. See
# ../../../docs/operations/environments.md for the full multi-env story.
ENVIRONMENT: str = _env("ENVIRONMENT", "dev").strip().lower()

# Wide open by default ONLY in dev (matches this project's original
# zero-setup posture). Any other environment defaults to allowing nothing,
# forcing CORS_ALLOWED_ORIGINS to be set explicitly once this service is
# actually reachable from a real browser origin outside localhost.
CORS_ALLOWED_ORIGINS: list[str] = [
    o.strip() for o in _env("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
] or (["*"] if ENVIRONMENT == "dev" else [])

# API-key auth on every non-liveness endpoint (see app/auth.py) -- required
# by default outside dev, optional in dev so the zero-setup local workflow
# this project started as still works with no .env edits. Set API_KEY and
# leave REQUIRE_AUTH unset to opt in early even in dev.
API_KEY: str | None = os.environ.get("API_KEY") or None
REQUIRE_AUTH: bool = _env_bool("REQUIRE_AUTH", ENVIRONMENT != "dev")


# --- Sibling services ------------------------------------------------------

# The ONLY LLM entrypoint this service is allowed to call. It must never talk
# to OpenAI/Anthropic/Ollama directly - see README.md "Critical constraint".
LLM_GATEWAY_URL: str = _env("LLM_GATEWAY_URL", "http://llm-gateway:8001/v1/chat/completions")

# RAG / knowledge-base service queried by the `search_knowledge_base` tool.
RAG_SERVICE_URL: str = _env("RAG_SERVICE_URL", "http://rag-service:8002/query")

# Feast's online feature store, served over HTTP via `feast serve` -- see
# app/feature_store_client.py. Consulted live on every /chat turn (when a
# customer_id is given) to feed the urgency classifier real customer-context
# features, not just message text.
FEATURE_STORE_URL: str = _env("FEATURE_STORE_URL", "http://feature-store:6566")

# --- LLM behavior ------------------------------------------------------

# The llm-gateway is OpenAI-schema-compatible but does not require a real
# provider key; it accepts any non-empty string and applies its own routing.
LLM_GATEWAY_API_KEY: str = _env("LLM_GATEWAY_API_KEY", "not-needed-llm-gateway-handles-auth")
LLM_MODEL: str = _env("LLM_MODEL", "gpt-4o-mini")
LLM_REQUEST_TIMEOUT_SECONDS: float = float(_env("LLM_REQUEST_TIMEOUT_SECONDS", "30"))

# --- LangGraph loop guard ----------------------------------------------

# Hard ceiling on router<->tool round-trips in a single /chat turn. This is
# the explicit, declarative equivalent of the hand-rolled "anti-ping-pong"
# counters a LangChain AgentExecutor-based coordinator needs to maintain by
# hand. See app/graph.py for how this is wired as a graph edge.
MAX_ITERATIONS: int = int(_env("MAX_ITERATIONS", "5"))

# --- Live MLflow model registry (see app/model_registry.py) -------------
# agent-service classifies every chat message's urgency using whichever
# version is tagged URGENCY_MODEL_ALIAS in MLflow's registry -- this is the
# actual, observable MLOps integration: promoting/rolling back the alias in
# MLflow (services/mlflow/train_and_log.py) changes this service's live
# behavior without a redeploy. Never hardcode a version number here; that
# would defeat the entire point of a model registry.
MLFLOW_TRACKING_URI: str = _env("MLFLOW_TRACKING_URI", "http://mlflow:5000")
URGENCY_MODEL_NAME: str = _env("URGENCY_MODEL_NAME", "support-urgency-classifier")
URGENCY_MODEL_ALIAS: str = _env("URGENCY_MODEL_ALIAS", "champion")
MODEL_REFRESH_SECONDS: float = float(_env("MODEL_REFRESH_SECONDS", "60"))

# --- Langfuse tracing (optional, no-op if unset) ------------------------

LANGFUSE_HOST: str | None = os.environ.get("LANGFUSE_HOST")
LANGFUSE_PUBLIC_KEY: str | None = os.environ.get("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY: str | None = os.environ.get("LANGFUSE_SECRET_KEY")

# --- Online (production-traffic) evaluation, see app/online_eval.py -----
# A DIFFERENT thing from eval-service's scenario-based offline judge: this
# samples a fraction of REAL /chat traffic and scores it after the response
# has already been sent, attaching the score to that request's own Langfuse
# trace. Real production setups typically sample much lower (1-20%); this
# defaults higher (30%) purely so the pattern is easy to observe in a local
# demo without sending dozens of chat messages. Set to "0" to disable.
ONLINE_EVAL_SAMPLE_RATE: float = float(_env("ONLINE_EVAL_SAMPLE_RATE", "0.3"))
JUDGE_MODEL: str = _env("JUDGE_MODEL", "gpt-4o-mini")

# --- Service metadata ----------------------------------------------------

SERVICE_NAME: str = "agent-service"
SERVICE_PORT: int = int(_env("SERVICE_PORT", "8003"))
