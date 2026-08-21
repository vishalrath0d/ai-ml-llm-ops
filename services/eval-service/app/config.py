"""Centralized environment configuration for eval-service.

All values have sane defaults for running inside the project's docker-compose
network (service hostnames), and can be overridden for local/dev/test use.
"""
import os


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# Postgres connection string. SQLAlchemy 2.0 + psycopg2 driver.
DATABASE_URL: str = os.getenv(
    "DATABASE_URL", "postgresql://aiops:aiops@postgres:5432/eval_db"
)

# Sibling service under test: the hands-on project's agent-service.
AGENT_SERVICE_URL: str = os.getenv("AGENT_SERVICE_URL", "http://agent-service:8003")

# Sibling LLM gateway used both to drive scenario setup and to run the judge.
LLM_GATEWAY_URL: str = os.getenv("LLM_GATEWAY_URL", "http://llm-gateway:8001")

# Model name to request from llm-gateway for judging. Must correspond to a
# model llm-gateway knows how to route; adjust to whatever the gateway's
# provider config supports.
JUDGE_MODEL: str = os.getenv("JUDGE_MODEL", "gpt-4o-mini")

# HTTP timeout (seconds) applied to both the agent-service and llm-gateway calls.
HTTP_TIMEOUT_SECONDS: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))

# Number of user turns to drive per scenario run (1 = single-turn, 2-3 = short
# multi-turn conversation). Extra turns use generic follow-up prompts.
MAX_CONVERSATION_TURNS: int = int(os.getenv("MAX_CONVERSATION_TURNS", "2"))

# Whether to auto-insert the demo scenarios on startup if the table is empty.
SEED_ON_STARTUP: bool = _bool_env("SEED_ON_STARTUP", True)

# Optional Langfuse tracing -- see app/tracing.py. All three unset (the
# default when running standalone/in tests) makes tracing a no-op.
LANGFUSE_HOST: str | None = os.getenv("LANGFUSE_HOST") or None
LANGFUSE_PUBLIC_KEY: str | None = os.getenv("LANGFUSE_PUBLIC_KEY") or None
LANGFUSE_SECRET_KEY: str | None = os.getenv("LANGFUSE_SECRET_KEY") or None

# --- Environment / multi-env posture -------------------------------------
# See agent-service/app/config.py's identical block for the full rationale
# and ../../../docs/environments.md for the project-wide story.
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "dev").strip().lower()

CORS_ALLOWED_ORIGINS: list[str] = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
] or (["*"] if ENVIRONMENT == "dev" else [])

API_KEY: str | None = os.getenv("API_KEY") or None
REQUIRE_AUTH: bool = _bool_env("REQUIRE_AUTH", ENVIRONMENT != "dev")
