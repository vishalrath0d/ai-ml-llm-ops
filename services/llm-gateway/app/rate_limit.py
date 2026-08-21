"""Per-client-IP rate limiting, as ASGI middleware.

Not `slowapi` -- tried it first, and its `@limiter.limit(...)` route
decorator turned out to fight this codebase's `from __future__ import
annotations` (used throughout for clean type hints) badly enough that
FastAPI stopped recognizing the chat-completion endpoint's Pydantic body
model at all (a real, reproduced failure: every request 422'd with "field
required" on the body param, then a second failure trying the documented
`= Body(...)` workaround -- see git history/PR discussion for the exact
errors if curious). A middleware sidesteps the whole class of problem the
same way ApiKeyMiddleware already does, at the cost of writing the counting
logic by hand instead of importing it.

This is a fixed-window counter (not sliding-window/token-bucket) -- simpler
to reason about, and the inaccuracy at window boundaries (a client could in
theory send ~2x the limit across a boundary) doesn't matter for what this
is actually for: a backstop against a runaway loop or retry storm, not a
precise per-second product quota.
"""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

EXEMPT_PATHS = {"/health", "/metrics"}
_WINDOW_SECONDS = 60

# client_ip -> (window_start_epoch_seconds, count_in_window). Module-level
# (not an attribute on the middleware instance) specifically so tests can
# reset it directly via `reset_windows()` -- Starlette builds/caches its
# middleware stack lazily and doesn't hand back a live reference to a
# specific middleware instance, so an instance attribute would have no
# test-reachable reset path. Fine for a single-replica local stack; a
# horizontally-scaled deployment would need this in Redis instead (same
# "in-memory state doesn't survive a second replica" note the FinOps/
# production-judgment doc's audit already flags elsewhere).
_windows: dict[str, tuple[float, int]] = {}


def reset_windows() -> None:
    """Test-only: clear all rate-limit counters between tests so one test's
    limit-exhausting requests can't leak into an unrelated later test."""
    _windows.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window_start, count = _windows.get(client_ip, (now, 0))

        if now - window_start >= _WINDOW_SECONDS:
            window_start, count = now, 0

        count += 1
        _windows[client_ip] = (window_start, count)

        if count > settings.RATE_LIMIT_PER_MINUTE:
            retry_after = max(1, int(_WINDOW_SECONDS - (now - window_start)))
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded ({settings.RATE_LIMIT_PER_MINUTE}/minute). Retry later."},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)
