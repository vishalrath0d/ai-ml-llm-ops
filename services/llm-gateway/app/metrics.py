"""Prometheus metrics for the gateway, exposed at GET /metrics."""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUEST_COUNT = Counter(
    "llm_gateway_requests_total",
    "Total number of chat completion requests handled, by provider and outcome",
    ["provider", "status"],  # status: success | error | chaos_error
)

REQUEST_LATENCY = Histogram(
    "llm_gateway_request_latency_seconds",
    "Latency of chat completion requests, by provider",
    ["provider"],
)

TOKEN_USAGE = Counter(
    "llm_gateway_tokens_total",
    "Total tokens processed, by provider and kind (prompt|completion)",
    ["provider", "kind"],
)

# Illustrative $/1K-token rates (prompt, completion) -- NOT fetched from any
# provider's real-time pricing API. Provider pricing changes over time and
# varies by exact model/region/contract tier; treat these as a starting
# point to edit to match your actual rates, not a source of truth. This is
# the direct fix for the gap 02-concepts/04-finops-and-production-judgment.md
# §3/§11 names explicitly: llm_gateway_tokens_total already existed, but
# nothing derived a $ figure from it, which is exactly the FinOps
# visibility gap that doc argues is worth closing before it becomes an
# actual budget surprise.
COST_PER_1K_TOKENS_USD: dict[str, tuple[float, float]] = {
    "gemini": (0.00030, 0.00250),  # gemini-3.6-flash ballpark
    "openai": (0.00015, 0.00060),  # gpt-4o-mini ballpark
    "anthropic": (0.00100, 0.00500),  # claude-haiku ballpark
    # Self-hosted: no per-token API cost. The real cost is host compute
    # (see 02-concepts/01-foundational-ai-ml.md §9 and 04-finops-and-
    # production-judgment.md §1) -- not zero, just not a per-token figure
    # this metric can express.
    "ollama": (0.0, 0.0),
}

COST_USD_TOTAL = Counter(
    "llm_gateway_cost_usd_total",
    "Estimated USD cost of tokens processed, by provider -- derived from "
    "llm_gateway_tokens_total via the static COST_PER_1K_TOKENS_USD rate "
    "table in this file. Illustrative rates, not real-time provider "
    "pricing -- see that table's own comment before trusting this for a "
    "real budget decision.",
    ["provider"],
)


def record_cost(provider: str, prompt_tokens: int, completion_tokens: int) -> None:
    prompt_rate, completion_rate = COST_PER_1K_TOKENS_USD.get(provider, (0.0, 0.0))
    cost = (prompt_tokens / 1000.0) * prompt_rate + (completion_tokens / 1000.0) * completion_rate
    if cost:
        COST_USD_TOTAL.labels(provider=provider).inc(cost)

GUARDRAIL_TRIGGERED = Counter(
    "llm_gateway_guardrail_triggered_total",
    "Number of times an inline guardrail blocked or redacted a request/response, by guardrail and direction.",
    ["guardrail", "direction"],  # direction: input | output
)


def metrics_response() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
