"""Sampled, asynchronous LLM-as-judge scoring of live /chat traffic --
the "online evaluation" pattern real production LLM systems use, which is a
genuinely different thing from eval-service's scenario-based offline judge:

  * eval-service (see services/eval-service/) grades a fixed, curated set of
    scenarios against a known `success_criteria`, offline/on-demand -- a
    testing practice, not a runtime behavior. See the root README's "How it
    all works" section 2 (and section 3 for a side-by-side comparison of
    this module, eval-service, and llm-gateway's guardrails).
  * This module grades a *sample of real live requests*, for which there is
    no predefined success_criteria (an arbitrary user message doesn't come
    with one), purely as a production quality signal. It runs strictly
    *after* the /chat response has already been sent to the caller -- see
    app/main.py, which schedules `run_online_eval` via FastAPI's
    `BackgroundTasks` rather than awaiting it inline. This must never add
    latency to a real request; a judge call failing or running slowly here
    is invisible to the user by construction.

The resulting score is attached to that request's own Langfuse trace via
`Langfuse.create_score(trace_id=...)`, so it shows up alongside the trace a
human would already be looking at if they opened it up -- not as a separate,
disconnected report.
"""
from __future__ import annotations

import json
import logging
import random
import re

import httpx

from app.config import JUDGE_MODEL, LLM_GATEWAY_URL, ONLINE_EVAL_SAMPLE_RATE
from app.metrics import ONLINE_EVAL_RUNS_TOTAL, ONLINE_EVAL_SCORE
from app.tracing import get_langfuse_client

logger = logging.getLogger("agent-service.online_eval")

_JUDGE_SYSTEM_PROMPT = (
    "You are grading a single customer-support chat response for quality. "
    "You are NOT given any expected answer -- judge only whether the "
    "response is coherent, on-topic for the user's message, appropriately "
    "concise, and free of confidently-stated specifics that look fabricated "
    "(invented policy numbers, invented names, invented links). "
    'Respond with ONLY a JSON object: {"score_percent": <0-100 integer>, '
    '"reason": "<one short sentence>"}. No other text, no markdown fences.'
)

# The judge is asked for raw JSON but small local models sometimes wrap it in
# prose or a code fence anyway -- pull out the first {...} block rather than
# requiring an exact match, same defensive pattern eval-service's judge uses.
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def is_sampled() -> bool:
    """The sampling coin-flip, called BEFORE scheduling any background work
    so an un-sampled request (the common case) pays zero cost -- not even a
    BackgroundTasks registration."""
    return random.random() < ONLINE_EVAL_SAMPLE_RATE


def run_online_eval(trace_id: str, user_message: str, response_text: str) -> None:
    """Judge one already-answered turn and attach the score to its trace.

    Runs inside a FastAPI background task, after the HTTP response has been
    sent. Deliberately never raises -- a failure here is a missed data point
    for a dashboard, not a user-facing problem, so every failure mode is
    caught and logged rather than propagated.
    """
    try:
        payload = {
            "model": JUDGE_MODEL,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"User message: {user_message}\n\nAgent response: {response_text}",
                },
            ],
        }
        resp = httpx.post(LLM_GATEWAY_URL, json=payload, timeout=60.0)
        resp.raise_for_status()
        content = (resp.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""

        match = _JSON_BLOCK_RE.search(content)
        if not match:
            raise ValueError(f"judge did not return a JSON object: {content!r}")
        verdict = json.loads(match.group(0))
        score = float(verdict["score_percent"])
        reason = str(verdict.get("reason", ""))[:500]
    except Exception:
        ONLINE_EVAL_RUNS_TOTAL.labels(status="error").inc()
        logger.exception(
            "online eval judge call failed for trace_id=%s (non-fatal, response already sent)",
            trace_id,
        )
        return

    try:
        client = get_langfuse_client()
        if client is not None:
            client.create_score(
                name="online-quality",
                value=score,
                trace_id=trace_id,
                data_type="NUMERIC",
                comment=reason,
            )
            client.flush()
    except Exception:
        logger.exception("failed to write online eval score to Langfuse for trace_id=%s", trace_id)
        # The judge call itself succeeded -- still record the metric below,
        # even if Langfuse couldn't be reached to attach it to the trace.

    ONLINE_EVAL_SCORE.observe(score)
    ONLINE_EVAL_RUNS_TOTAL.labels(status="ok").inc()
    logger.info("online eval scored trace_id=%s score=%.0f reason=%s", trace_id, score, reason)
