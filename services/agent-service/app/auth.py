"""API-key auth, as ASGI middleware rather than a per-endpoint dependency.

Why middleware instead of `Depends(require_api_key)` on each route: this
project has grown enough endpoints (/chat, /admin/*, more added over time)
that a per-route dependency is one more thing to remember to add to every
NEW endpoint too -- easy to silently miss one. Middleware enforces this for
every request by construction, with an explicit exemption list for the two
routes that must stay reachable with no key: `/health` (container
healthchecks, orchestrator liveness/readiness probes) and `/metrics`
(Prometheus's own scraper). A real production deployment would more likely
put those two behind network policy instead of an app-level exemption list
-- this is the honest, simplest equivalent for a stack that runs on one
docker-compose network with no such policy layer.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import config

EXEMPT_PATHS = {"/health", "/metrics"}


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Reads config.API_KEY / config.REQUIRE_AUTH through the module
        # (not `from app.config import API_KEY, REQUIRE_AUTH`) deliberately
        # -- a direct name import snapshots the value at import time, so
        # nothing afterward (a test's monkeypatch, or any future live
        # reconfiguration) could ever actually change it. Qualified access
        # re-reads the module's current attribute on every request instead.
        #
        # OPTIONS is exempt too -- a browser's CORS preflight request never
        # carries a custom X-API-Key header (that's the whole point of a
        # preflight: asking permission BEFORE sending the real request with
        # its real headers), so blocking it here would break CORS for every
        # real, correctly-authenticated request behind it.
        if not config.REQUIRE_AUTH or request.method == "OPTIONS" or request.url.path in EXEMPT_PATHS:
            return await call_next(request)
        provided = request.headers.get("x-api-key")
        if not config.API_KEY or provided != config.API_KEY:
            return JSONResponse(status_code=401, content={"detail": "Missing or invalid X-API-Key header"})
        return await call_next(request)
