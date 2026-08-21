"""API-key auth middleware -- see agent-service/app/auth.py for the full
rationale (middleware over per-route Depends, why /health and /metrics are
exempt, why OPTIONS is exempt)."""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import config

EXEMPT_PATHS = {"/health", "/metrics"}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Qualified `config.X` access, not `from app.config import X` --
        # see agent-service/app/auth.py's identical comment for why a
        # direct name import would make this un-reconfigurable (and
        # un-mockable in tests) after process start.
        if not config.REQUIRE_AUTH or request.method == "OPTIONS" or request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        provided = request.headers.get("x-api-key")
        if not config.API_KEY or provided != config.API_KEY:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid X-API-Key header"})
        return await call_next(request)
