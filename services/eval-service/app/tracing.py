"""Langfuse tracing wrapper for eval-service.

Every scenario run gets one Langfuse trace: the scenario's opening message
and success_criteria as input, the driven conversation transcript and the
judge's verdict as output. This is deliberately a *separate* trace from
whatever agent-service/llm-gateway create while that conversation runs --
Postgres's own TestRun table is this service's durable record either way
(see the root README's "How it all works" section 6 for why); Langfuse here just adds
the same per-request drill-down the other two traced services get, so a
judge verdict you don't understand can be inspected the same way a bad chat
reply can.

Same defensive contract as the other two tracing.py modules in this
project: missing env vars / missing package / an unreachable Langfuse all
degrade to a silent no-op, never an exception that could fail an eval run.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

logger = logging.getLogger("eval_service.tracing")

_langfuse_client: Any = None
_tracing_enabled = False

if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY:
    try:
        from langfuse import Langfuse  # type: ignore

        _kwargs = {"public_key": LANGFUSE_PUBLIC_KEY, "secret_key": LANGFUSE_SECRET_KEY}
        if LANGFUSE_HOST:
            _kwargs["host"] = LANGFUSE_HOST
        _langfuse_client = Langfuse(**_kwargs)
        _tracing_enabled = True
        logger.info("Langfuse tracing enabled (host=%s)", LANGFUSE_HOST or "default")
    except ImportError:
        logger.info("langfuse package not installed -- tracing is a no-op")
    except Exception as exc:  # pragma: no cover - defensive, never crash on init
        logger.warning("Failed to initialize Langfuse client, tracing disabled: %s", exc)
        _langfuse_client = None
        _tracing_enabled = False
else:
    logger.info("LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set -- tracing is a no-op")


def trace_eval_run(
    scenario_name: str,
    opening_message: str,
    success_criteria: str,
    transcript: list,
    score_percent: Optional[float],
    result: str,
    reason: str,
    latency_ms: int,
) -> None:
    """Record one scenario run as a single Langfuse trace. Fire-and-forget --
    called after the TestRun row is already committed, so a tracing failure
    here can never affect the eval result itself."""
    if not _tracing_enabled or _langfuse_client is None:
        return
    try:
        trace = _langfuse_client.trace(
            name="eval-service.scenario-run",
            input={"scenario": scenario_name, "opening_message": opening_message, "success_criteria": success_criteria},
            metadata={"latency_ms": latency_ms},
        )
        trace.update(
            output={
                "transcript": transcript,
                "score_percent": score_percent,
                "result": result,
                "reason": reason,
            },
            level="ERROR" if result == "fail" else "DEFAULT",
        )
    except Exception as exc:  # pragma: no cover - never let tracing break an eval run
        logger.debug("Langfuse trace_eval_run() failed: %s", exc)


def is_enabled() -> bool:
    return _tracing_enabled
