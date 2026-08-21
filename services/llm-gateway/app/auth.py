"""API-key auth middleware -- see agent-service/app/auth.py for the full
rationale (middleware over per-route Depends, why /health and /metrics are
exempt, why OPTIONS is exempt). This service reads settings.API_KEY /
settings.REQUIRE_AUTH rather than module-level constants, matching this
service's own Settings-object config style."""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import settings

EXEMPT_PATHS = {"/health", "/metrics"}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.REQUIRE_AUTH or request.method == "OPTIONS" or request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        provided = request.headers.get("x-api-key")
        if not settings.API_KEY or provided != settings.API_KEY:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid X-API-Key header"})
        return await call_next(request)
