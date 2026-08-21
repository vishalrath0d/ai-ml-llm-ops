"""rag-service — a standalone retrieval microservice.

Mirrors two real production patterns:

1. A real internal data-engineering Slack bot's hybrid retrieval design: hand
   curated keyword -> document routing checked first, semantic vector search
   only as a fallback (see app/retrieval.py for the full rationale).
2. A common "RAG is a separate service, not inline" production pattern: knowledge
   base search is exposed here over HTTP so any number of conversational
   agents can call it without each reimplementing retrieval, embedding
   model management, or a vector store.
"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.auth import ApiKeyMiddleware
from app.config import AUTO_SEED_ON_STARTUP, CORS_ALLOWED_ORIGINS, ENVIRONMENT
from app.embeddings import warm_up as warm_up_embedding_model
from app.ingestion import ingest_document
from app.logging_setup import configure_logging
from app.metrics import INGEST_COUNT, QUERY_COUNT, QUERY_LATENCY_SECONDS
from app.models import IngestRequest, IngestResponse, QueryRequest, QueryResponse
from app.retrieval import retrieve
from app.seeding import seed_if_missing
from app.store import count as store_count
from app.tracing import trace_query

configure_logging(ENVIRONMENT)
logger = logging.getLogger("rag_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the embedding model NOW, before accepting traffic -- not on
    # whichever real request happens to be first. See app/embeddings.py's
    # module docstring for the real incident (a live /chat request timing
    # out) this fixes. Deliberately before seeding: seeding itself needs
    # the model too, so this also means seeding no longer silently pays
    # the same cost in whichever request happens to trigger it later.
    try:
        logger.info("Warming up the embedding model...")
        warm_up_embedding_model()
        logger.info("Embedding model warm-up complete.")
    except Exception:  # pragma: no cover - defensive; don't crash boot on warm-up failure
        logger.exception("Embedding model warm-up failed; first real request will pay this cost instead.")

    if AUTO_SEED_ON_STARTUP:
        try:
            seed_if_missing()
        except Exception:  # pragma: no cover - defensive; don't crash boot on seed failure
            logger.exception("Seeding failed on startup; continuing without seed data.")
    yield


app = FastAPI(
    title="rag-service",
    description="Hybrid (keyword-trigger + semantic) retrieval microservice over a persistent Chroma store.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS_ALLOWED_ORIGINS defaults to "*" only in dev (see app/config.py) --
# outside dev it defaults to allowing nothing until set explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added AFTER CORSMiddleware -- see agent-service/app/main.py's identical
# comment for why that ordering matters (CORS preflight must run first).
app.add_middleware(ApiKeyMiddleware)


@app.get("/health")
def health():
    return {"status": "ok", "chunks_in_store": store_count()}


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/ingest", response_model=IngestResponse)
def ingest(request: IngestRequest):
    try:
        result = ingest_document(
            text=request.text,
            url=request.url,
            doc_id=request.doc_id,
            source=request.source,
            metadata=request.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # e.g. URL fetch failure
        raise HTTPException(status_code=502, detail=f"ingest failed: {exc}") from exc

    INGEST_COUNT.inc()
    return IngestResponse(**result)


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest):
    start = time.perf_counter()
    results, retrieval_path, matched_keyword, routed_doc_id = retrieve(
        request.query, k=request.k, mode=request.mode
    )
    elapsed = time.perf_counter() - start

    QUERY_COUNT.labels(mode=request.mode, retrieval_path=retrieval_path).inc()
    QUERY_LATENCY_SECONDS.labels(mode=request.mode).observe(elapsed)
    trace_query(
        query_text=request.query,
        mode=request.mode,
        retrieval_path=retrieval_path,
        matched_keyword=matched_keyword,
        routed_doc_id=routed_doc_id,
        results=results,
        latency_seconds=elapsed,
    )

    return QueryResponse(
        query=request.query,
        mode=request.mode,
        retrieval_path=retrieval_path,
        matched_keyword=matched_keyword,
        routed_doc_id=routed_doc_id,
        results=results,
    )
