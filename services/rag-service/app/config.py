"""Central configuration for the rag-service, driven entirely by env vars so the
same image behaves correctly both in docker-compose and in a local dev venv."""
import os

# Where ChromaDB persists its embedded (no-server) database.
# In docker-compose this is bind/volume-mounted; locally it defaults to a
# project-relative folder so `python seed.py` / pytest work out of the box.
CHROMA_PERSIST_DIR = os.environ.get(
    "CHROMA_PERSIST_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chroma_data"),
)

COLLECTION_NAME = os.environ.get("CHROMA_COLLECTION_NAME", "kb_documents")

# Small, CPU-friendly sentence-transformers embedding model.
EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")

# Curated keyword -> doc_id routing table for the "hybrid" retrieval mode.
KEYWORD_TRIGGERS_PATH = os.environ.get(
    "KEYWORD_TRIGGERS_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "keyword_triggers.yaml"),
)

# Recursive chunking parameters. ~4 chars/token is a reasonable rule of thumb
# for English text with the MiniLM tokenizer, so 2000 chars ~= 500 tokens.
CHUNK_SIZE_CHARS = int(os.environ.get("CHUNK_SIZE_CHARS", "2000"))
CHUNK_OVERLAP_CHARS = int(os.environ.get("CHUNK_OVERLAP_CHARS", "200"))

DEFAULT_TOP_K = int(os.environ.get("DEFAULT_TOP_K", "5"))

# Whether to seed the fictional-product knowledge base on FastAPI startup.
# Disabled automatically during pytest runs (see tests/conftest.py) so tests
# control seeding explicitly.
AUTO_SEED_ON_STARTUP = os.environ.get("AUTO_SEED_ON_STARTUP", "true").lower() == "true"

# Optional Langfuse tracing -- see app/tracing.py. All three unset (the
# default when running standalone/in tests) makes tracing a no-op.
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST") or None
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY") or None
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY") or None

# --- Environment / multi-env posture -------------------------------------
# See agent-service/app/config.py's identical block for the full rationale
# and ../../../docs/environments.md for the project-wide story.
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev").strip().lower()

CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
] or (["*"] if ENVIRONMENT == "dev" else [])

API_KEY = os.environ.get("API_KEY") or None
REQUIRE_AUTH = os.environ.get(
    "REQUIRE_AUTH", "true" if ENVIRONMENT != "dev" else "false"
).strip().lower() in ("1", "true", "yes", "on")
