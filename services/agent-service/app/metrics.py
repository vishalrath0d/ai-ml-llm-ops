"""Prometheus metrics for agent-service.

Exposed at GET /metrics via prometheus_client's default text exposition
format (see app/main.py).
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

CHAT_REQUESTS_TOTAL = Counter(
    "agent_service_chat_requests_total",
    "Total number of /chat requests handled.",
    ["status"],
)

CHAT_LATENCY_SECONDS = Histogram(
    "agent_service_chat_latency_seconds",
    "End-to-end latency of a /chat request, in seconds.",
)

TOOL_CALLS_TOTAL = Counter(
    "agent_service_tool_calls_total",
    "Number of times each tool was invoked by the LangGraph agent.",
    ["tool_name"],
)

# --- Live MLflow model registry integration (see app/model_registry.py) ---
# This is the concrete AIOps-visible signal that a model promotion/rollback
# in MLflow actually reached this running service: watch this gauge change
# value the moment someone flips the `champion` alias + reloads.
URGENCY_MODEL_VERSION_LOADED = Gauge(
    "agent_service_urgency_model_version_loaded",
    "MLflow registry version number currently loaded for the urgency classifier's `champion` alias (0 if none loaded yet).",
)

URGENCY_CLASSIFICATIONS_TOTAL = Counter(
    "agent_service_urgency_classifications_total",
    "Number of chat messages classified by urgency label.",
    ["label"],
)

# --- Online (production-traffic) evaluation, see app/online_eval.py -------
ONLINE_EVAL_RUNS_TOTAL = Counter(
    "agent_service_online_eval_runs_total",
    "Number of sampled-live-traffic online eval judge calls, by outcome.",
    ["status"],  # ok | error
)

ONLINE_EVAL_SCORE = Histogram(
    "agent_service_online_eval_score_percent",
    "Judge-assigned quality score (0-100) for sampled live /chat responses.",
    buckets=(0, 20, 40, 50, 60, 70, 80, 90, 95, 100),
)
