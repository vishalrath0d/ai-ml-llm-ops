# rag-service

A standalone retrieval microservice: chunk + embed + store documents, then
query them over HTTP. Runs on **port 8002**.

## What this mirrors

This service is a hands-on teaching mirror of two real production patterns:

1. **A real internal data-engineering Slack bot's hybrid retrieval design.**
   That bot does *not* embed every document and always
   fall back to a vector search. Naive "embed everything, always do a
   nearest-neighbor search" RAG is simple, but it's imprecise and pays an
   embedding-compute + vector-search cost on every single query — even for
   the handful of query patterns the team already knows the right answer to.
   Instead, it checks a small, hand-curated table of keyword -> document
   routes first. If a query obviously matches a known topic ("billing",
   "forgot password", "export data", ...), it's routed straight to that
   document. Only queries that don't match anything in the table fall
   through to full semantic (embedding) search.

   `rag-service` reimplements exactly this as its `hybrid` query mode, with
   the routing table in [`keyword_triggers.yaml`](./keyword_triggers.yaml)
   and the logic in [`app/retrieval.py`](./app/retrieval.py). A `naive` mode
   is also implemented, which always does semantic search regardless — so
   you can run the *same query* in both modes and literally watch the two
   retrieval strategies diverge (see below).

2. **A common "RAG is a separate service, not inline" production pattern.**
   Rather than every conversational-AI agent embedding its own copy of a
   vector store, its own chunking logic, and its own embedding model,
   knowledge-base search is exposed here as a single, independently
   deployable HTTP service. Any number of downstream agents/backends call
   `POST /query`; none of them need to know or care whether the answer came
   from a keyword-routed document or a semantic search, how the store is
   embedded, or which embedding model is loaded. This is the same reason a
   real conversational-AI backend delegates retrieval out to its own
   retrieval service instead of reimplementing it per-agent.

## Architecture

- **FastAPI** app on port 8002.
- **ChromaDB**, embedded mode (`chromadb.PersistentClient`) — no separate
  Chroma server container. Data persists to `CHROMA_PERSIST_DIR`
  (`/data/chroma` in Docker, backed by a named volume in
  [`compose.fragment.yml`](./compose.fragment.yml)).
- **sentence-transformers `all-MiniLM-L6-v2`** for embeddings — small
  (~90MB), fast, CPU-only, no GPU or external embedding API needed.
- **Recursive, sentence-aware chunking** via
  `langchain-text-splitters`' `RecursiveCharacterTextSplitter` (~500 tokens
  per chunk, ~10% overlap — see `CHUNK_SIZE_CHARS`/`CHUNK_OVERLAP_CHARS` in
  [`app/config.py`](./app/config.py)).
- Seeded on first startup (or via `python seed.py`) with an 8-document
  fictional "Onwly" help-center knowledge base
  ([`app/seed_data.py`](./app/seed_data.py)) — a generic SaaS ticketing
  product, **not** real production company content — so `/query` has something real
  to retrieve immediately after `docker compose up`. Seeding is idempotent:
  restarting the container does not re-ingest or duplicate chunks.

## API

### `POST /ingest`

```json
{
  "doc_id": "optional-stable-id",
  "text": "raw text to ingest",
  "source": "optional human-readable label",
  "metadata": {"any": "extra fields"}
}
```

...or fetch-and-ingest a URL instead of `text`:

```json
{ "url": "https://example.com/some-help-article", "source": "example.com" }
```

Response: `{"doc_id": "...", "source": "...", "chunks_ingested": 3}`

### `POST /query`

```json
{ "query": "How do I reset my password?", "k": 5, "mode": "hybrid" }
```

`mode` is `"hybrid"` (default) or `"naive"`.

Response:

```json
{
  "query": "How do I reset my password?",
  "mode": "hybrid",
  "retrieval_path": "keyword_trigger",
  "matched_keyword": "password reset",
  "routed_doc_id": "doc_login_troubleshooting",
  "results": [
    {"chunk_id": "...", "text": "...", "score": 0.81, "doc_id": "doc_login_troubleshooting", "source": "help-center/troubleshooting-login", "chunk_index": 0}
  ]
}
```

`retrieval_path` is always either `"keyword_trigger"` (hybrid mode found a
match in `keyword_triggers.yaml` and searched only within that document) or
`"semantic"` (full vector search across every ingested document — either
because hybrid mode found no keyword match, or because `mode="naive"` was
requested and never checked the table at all).

### `GET /health`

`{"status": "ok", "chunks_in_store": 42}`

### `GET /metrics`

Prometheus exposition format: `rag_ingest_total` (Counter),
`rag_query_total{mode,retrieval_path}` (Counter),
`rag_query_latency_seconds{mode}` (Histogram).

## Seeing the two retrieval strategies diverge

This is the whole point of the teaching example — run the identical query
in both modes and compare `retrieval_path`:

```bash
# Naive: always semantic search, every time.
curl -s localhost:8002/query -H 'content-type: application/json' \
  -d '{"query": "I have a question about billing and my last invoice", "mode": "naive"}' | jq .retrieval_path
# -> "semantic"

# Hybrid: "billing" is in keyword_triggers.yaml, so it's routed straight
# to doc_billing instead of doing a full semantic search across every
# ingested document.
curl -s localhost:8002/query -H 'content-type: application/json' \
  -d '{"query": "I have a question about billing and my last invoice", "mode": "hybrid"}' | jq '{retrieval_path, matched_keyword, routed_doc_id}'
# -> {"retrieval_path": "keyword_trigger", "matched_keyword": "billing", "routed_doc_id": "doc_billing"}
```

Try a query that isn't in the keyword table (e.g. `"what happens if a
customer wants to close their account entirely"`) in `hybrid` mode and
you'll see `retrieval_path: "semantic"` there too — hybrid mode only
diverges from naive when a keyword actually matches; everything else falls
through identically to full semantic search.

## Running locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python seed.py                 # optional — the app also seeds on startup
uvicorn app.main:app --reload --port 8002
```

```bash
pytest
```

## Running via Docker / compose

```bash
docker build -t rag-service .
docker run -p 8002:8002 -v rag_chroma_data:/data/chroma rag-service
```

Or merge [`compose.fragment.yml`](./compose.fragment.yml) into the project's
top-level compose file to run it alongside sibling services.

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `CHROMA_PERSIST_DIR` | `./chroma_data` (local) / `/data/chroma` (Docker) | Where the embedded Chroma DB persists to disk |
| `CHROMA_COLLECTION_NAME` | `kb_documents` | Chroma collection name |
| `EMBEDDING_MODEL_NAME` | `all-MiniLM-L6-v2` | sentence-transformers model |
| `KEYWORD_TRIGGERS_PATH` | `./keyword_triggers.yaml` | Hybrid-mode routing table |
| `CHUNK_SIZE_CHARS` / `CHUNK_OVERLAP_CHARS` | `2000` / `200` | ~500-token chunks (≈4 chars/token) with 10% overlap |
| `AUTO_SEED_ON_STARTUP` | `true` | Seed the fictional KB on FastAPI startup if missing |
