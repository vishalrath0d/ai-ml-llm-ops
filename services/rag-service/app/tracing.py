"""Langfuse tracing wrapper for rag-service.

Every /query call is wrapped in a single Langfuse trace: the query text as
input, the retrieval path taken (keyword_trigger vs. semantic) and returned
chunks as output/metadata. This service never calls an LLM itself, so there
is no "generation" here the way there is in llm-gateway -- just one trace
per retrieval, which is enough to answer "what did the KB actually return
for this query" when debugging a bad agent-service answer.

Deliberately defensive, same contract as the other two tracing.py modules in
this project: if LANGFUSE_HOST/LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY are
unset, or `langfuse` can't be imported, or Langfuse is unreachable, every
function here becomes a no-op and never raises. A retrieval must never fail
because tracing failed.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

logger = logging.getLogger("rag_service.tracing")

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


def trace_query(
    query_text: str,
    mode: str,
    retrieval_path: str,
    matched_keyword: Optional[str],
    routed_doc_id: Optional[str],
    results: list,
    latency_seconds: float,
) -> None:
    """Record one /query call as a single Langfuse trace. Fire-and-forget --
    never raises, never delays the actual HTTP response (called after the
    response body is already built)."""
    if not _tracing_enabled or _langfuse_client is None:
        return
    try:
        trace = _langfuse_client.trace(
            name="rag-service.query",
            input={"query": query_text, "mode": mode},
            metadata={
                "retrieval_path": retrieval_path,
                "matched_keyword": matched_keyword,
                "routed_doc_id": routed_doc_id,
                "latency_seconds": round(latency_seconds, 4),
            },
        )
        trace.update(
            output={
                "result_count": len(results),
                "top_chunk_ids": [r.chunk_id for r in results[:3]],
                "top_score": results[0].score if results else None,
            }
        )
    except Exception as exc:  # pragma: no cover - never let tracing break a request
        logger.debug("Langfuse trace_query() failed: %s", exc)


def is_enabled() -> bool:
    return _tracing_enabled
