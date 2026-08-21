"""Thin Langfuse wrapper that no-ops gracefully when unconfigured.

Langfuse env vars (LANGFUSE_HOST / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY)
are optional. If any are missing, `get_langfuse_client()` returns None and
`traced_chat_turn` becomes a plain contextmanager that yields a no-op span
object, so the rest of the code never has to branch on "is tracing enabled".

Written against langfuse-python v4 (OpenTelemetry-based client): there is no
`Langfuse.trace(...)` method in this version. Tracing a unit of work is done
with `client.start_as_current_observation(..., as_type="span")` as a context
manager, and trace-level attributes (like session_id) are attached via the
module-level `propagate_attributes(...)` context manager so every span
created underneath inherits them.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from app.config import LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY

logger = logging.getLogger("agent-service.tracing")

_client: Any | None = None
_client_initialized = False


class _NoOpSpan:
    """Stand-in for a Langfuse span when tracing is disabled."""

    def update(self, *args: Any, **kwargs: Any) -> "_NoOpSpan":
        return self

    def end(self, *args: Any, **kwargs: Any) -> None:
        return None

    def __getattr__(self, _name: str) -> Any:
        # Swallow any other Langfuse API surface (e.g. .score(), .create_event())
        def _noop(*args: Any, **kwargs: Any) -> "_NoOpSpan":
            return self

        return _noop


def get_langfuse_client() -> Any | None:
    """Return a singleton Langfuse client, or None if not configured/installed."""
    global _client, _client_initialized
    if _client_initialized:
        return _client
    _client_initialized = True

    if not (LANGFUSE_HOST and LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
        logger.info("Langfuse env vars not fully set; tracing disabled (no-op).")
        _client = None
        return None

    try:
        from langfuse import Langfuse

        _client = Langfuse(
            host=LANGFUSE_HOST,
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
        )
        logger.info("Langfuse tracing enabled (host=%s).", LANGFUSE_HOST)
    except Exception:  # noqa: BLE001 - tracing must never break the request path
        logger.exception("Failed to initialize Langfuse client; tracing disabled.")
        _client = None
    return _client


@contextmanager
def traced_chat_turn(session_id: str, user_message: str) -> Iterator[Any]:
    """Context manager wrapping one /chat turn in a Langfuse trace.

    Yields a span-like object with .update(...) so callers can attach the
    final output/metadata without caring whether tracing is actually active.
    Any real exception raised by the wrapped block (e.g. the agent graph
    failing) propagates through unchanged - this wrapper only isolates
    failures coming from the tracing SDK itself.
    """
    client = get_langfuse_client()
    if client is None:
        yield _NoOpSpan()
        return

    try:
        from langfuse import propagate_attributes

        span_cm = client.start_as_current_observation(
            name="agent-service.chat",
            as_type="span",
            input={"message": user_message},
            metadata={"session_id": session_id},
        )
        attrs_cm = propagate_attributes(session_id=session_id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to start Langfuse span; continuing without tracing.")
        yield _NoOpSpan()
        return

    with attrs_cm, span_cm as span:
        try:
            yield span
        finally:
            try:
                client.flush()
            except Exception:  # noqa: BLE001
                logger.exception("Langfuse flush() failed.")
