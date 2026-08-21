"""
Configuration for the llm-gateway service.

Two kinds of config live here, deliberately kept separate:

- `Settings`: resolved once from environment variables at process start.
  These are the "how do I even connect" knobs (provider choice, API keys,
  model names) that a real deployment sets via its container/orchestrator
  and does not expect to change without a restart.

- `ChaosState`: mutable, in-memory, and intended to be flipped *live* via
  `POST /admin/chaos` while the container keeps running. Environment
  variables (`CHAOS_LATENCY_MS`, `CHAOS_ERROR_RATE`) only seed the initial
  values -- see README.md for why a runtime endpoint is more practical than
  restarting the container for a hands-on chaos-engineering exercise.
"""
from __future__ import annotations

import os
from typing import List


def _env_chain(name: str, default: str) -> List[str]:
    """Parse a comma-separated provider list into an ordered, deduped,
    lowercased list -- e.g. "gemini, openai,,ollama" -> ["gemini", "openai",
    "ollama"]. Empty entries (trailing commas, blank env var) are dropped
    rather than producing a chain with a "" provider name in it."""
    raw = os.getenv(name, default)
    seen: dict[str, None] = {}
    for part in raw.split(","):
        name_ = part.strip().lower()
        if name_:
            seen[name_] = None
    return list(seen)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Settings:
    """Static-ish settings resolved from the environment. Plain mutable
    attributes on purpose (not a frozen/pydantic model) -- tests monkeypatch
    them directly to exercise the fallback/provider-selection logic without
    touching the real environment."""

    def __init__(self) -> None:
        # Ordered fallback chain, tried left to right until one succeeds --
        # see app/main.py's `_provider_chain()` for the two things that can
        # narrow this at request time (a provider erroring, or a request too
        # large for a given provider's context window). Default order is
        # deliberately Gemini-first (generous free tier, largest context
        # window of the four -- see GEMINI_CONTEXT_WINDOW below), then
        # OpenAI, then Anthropic, then Ollama last as the always-available,
        # no-API-key-required local backstop.
        self.LLM_PROVIDER_CHAIN: List[str] = _env_chain(
            "LLM_PROVIDER_CHAIN", "gemini,openai,anthropic,ollama"
        )

        self.OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
        self.OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b")
        self.OLLAMA_CONTEXT_WINDOW: int = _env_int("OLLAMA_CONTEXT_WINDOW", 4_096)

        self.OPENAI_API_KEY: str | None = os.getenv("OPENAI_API_KEY") or None
        self.OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.OPENAI_CONTEXT_WINDOW: int = _env_int("OPENAI_CONTEXT_WINDOW", 128_000)

        self.ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY") or None
        self.ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        self.ANTHROPIC_MAX_TOKENS: int = _env_int("ANTHROPIC_MAX_TOKENS", 1024)
        self.ANTHROPIC_CONTEXT_WINDOW: int = _env_int("ANTHROPIC_CONTEXT_WINDOW", 200_000)

        self.GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY") or None
        self.GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
        self.GEMINI_CONTEXT_WINDOW: int = _env_int("GEMINI_CONTEXT_WINDOW", 1_000_000)

        # Provider name -> context window, used for token-based routing (see
        # app/main.py's `_provider_chain()`): a request estimated to exceed a
        # given provider's window is skipped over in the chain rather than
        # sent to a provider guaranteed to reject it with a context-length
        # error. Ollama's default here (4,096) is not a guess -- it's the
        # real `n_ctx_slot` this project's own qwen2.5:0.5b was observed
        # running with.
        self.PROVIDER_CONTEXT_WINDOWS: dict[str, int] = {
            "ollama": self.OLLAMA_CONTEXT_WINDOW,
            "openai": self.OPENAI_CONTEXT_WINDOW,
            "anthropic": self.ANTHROPIC_CONTEXT_WINDOW,
            "gemini": self.GEMINI_CONTEXT_WINDOW,
        }

        self.LANGFUSE_HOST: str | None = os.getenv("LANGFUSE_HOST") or None
        self.LANGFUSE_PUBLIC_KEY: str | None = os.getenv("LANGFUSE_PUBLIC_KEY") or None
        self.LANGFUSE_SECRET_KEY: str | None = os.getenv("LANGFUSE_SECRET_KEY") or None

        self.HTTP_TIMEOUT_SECONDS: float = _env_float("HTTP_TIMEOUT_SECONDS", 120.0)

        # --- Environment / multi-env posture --------------------------
        # See ../../agent-service/app/config.py's identical block for the
        # full rationale and ../../../docs/operations/environments.md for the project-wide
        # story.
        self.ENVIRONMENT: str = os.getenv("ENVIRONMENT", "dev").strip().lower()

        cors_env = (os.getenv("CORS_ALLOWED_ORIGINS") or "").strip()
        self.CORS_ALLOWED_ORIGINS: list[str] = (
            [o.strip() for o in cors_env.split(",") if o.strip()]
            if cors_env
            else (["*"] if self.ENVIRONMENT == "dev" else [])
        )

        self.API_KEY: str | None = os.getenv("API_KEY") or None
        self.REQUIRE_AUTH: bool = _env_bool("REQUIRE_AUTH", self.ENVIRONMENT != "dev")

        # Requests per minute per client IP -- see app/rate_limit.py.
        # Deliberately generous: this is a backstop against a runaway loop
        # or an unbounded retry storm hitting real, billed provider APIs,
        # not a strict product-level quota.
        self.RATE_LIMIT_PER_MINUTE: int = _env_int("RATE_LIMIT_PER_MINUTE", 60)


settings = Settings()


class ChaosState:
    """Runtime-mutable chaos injection knobs.

    NOT re-read from the environment on every request -- that's the whole
    point. `CHAOS_LATENCY_MS` / `CHAOS_ERROR_RATE` only seed the *initial*
    values at container start; from then on, flip them live with:

        curl -X POST http://localhost:8001/admin/chaos \\
             -H 'Content-Type: application/json' \\
             -d '{"latency_ms": 2000, "error_rate": 0.25}'
    """

    def __init__(self) -> None:
        self.latency_ms: int = max(0, _env_int("CHAOS_LATENCY_MS", 0))
        self.error_rate: float = min(1.0, max(0.0, _env_float("CHAOS_ERROR_RATE", 0.0)))

    def as_dict(self) -> dict:
        return {"latency_ms": self.latency_ms, "error_rate": self.error_rate}


chaos_state = ChaosState()
