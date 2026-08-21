"""Langfuse tracing wrapper.

Every /v1/chat/completions call is wrapped in a Langfuse trace + generation
so token/cost/latency can be inspected per-request in the Langfuse UI. This
module is deliberately defensive: if `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY` are unset, or the `langfuse` package can't be imported,
or Langfuse itself is unreachable, every function here becomes a no-op and
never raises. Langfuse may simply not be running when someone tests this
service standalone -- that must never break a chat completion.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger("llm_gateway.tracing")

_langfuse_client: Any = None
_tracing_enabled = False

if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
    try:
        from langfuse import Langfuse  # type: ignore

        _kwargs = {
            "public_key": settings.LANGFUSE_PUBLIC_KEY,
            "secret_key": settings.LANGFUSE_SECRET_KEY,
        }
        if settings.LANGFUSE_HOST:
            _kwargs["host"] = settings.LANGFUSE_HOST
        _langfuse_client = Langfuse(**_kwargs)
        _tracing_enabled = True
        logger.info("Langfuse tracing enabled (host=%s)", settings.LANGFUSE_HOST or "default")
    except ImportError:
        logger.info("langfuse package not installed -- tracing is a no-op")
    except Exception as exc:  # pragma: no cover - defensive, never crash on init
        logger.warning("Failed to initialize Langfuse client, tracing disabled: %s", exc)
        _langfuse_client = None
        _tracing_enabled = False
else:
    logger.info("LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set -- tracing is a no-op")


def start_trace(name: str, input_data: Any, metadata: Optional[dict] = None) -> Any:
    """Start a Langfuse trace. Returns the trace object, or None if tracing
    is disabled/unavailable. Never raises."""
    if not _tracing_enabled or _langfuse_client is None:
        return None
    try:
        return _langfuse_client.trace(name=name, input=input_data, metadata=metadata or {})
    except Exception as exc:  # pragma: no cover - never let tracing break a request
        logger.debug("Langfuse trace() failed: %s", exc)
        return None


def log_generation(
    trace: Any,
    name: str,
    model: str,
    input_data: Any,
    output_data: Any,
    usage: dict,
) -> None:
    """Attach a generation (the actual LLM call) to an existing trace."""
    if trace is None:
        return
    try:
        trace.generation(
            name=name,
            model=model,
            input=input_data,
            output=output_data,
            usage=usage,
        )
        trace.update(output=output_data)
    except Exception as exc:  # pragma: no cover
        logger.debug("Langfuse generation logging failed: %s", exc)


def log_error(trace: Any, error: str) -> None:
    if trace is None:
        return
    try:
        trace.update(output={"error": error}, level="ERROR")
    except Exception as exc:  # pragma: no cover
        logger.debug("Langfuse error logging failed: %s", exc)


def is_enabled() -> bool:
    return _tracing_enabled
