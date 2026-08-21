# eval-service

A FastAPI microservice that LLM-judges the sibling `agent-service` in this
hands-on project. Port `8004`.

## What this mirrors

This is a simplified, local stand-in for a production LLM-eval service
(there: FastAPI + Celery + Mongo). The core pattern is the same:

1. You define a **Scenario**: an opening message to send to the agent under
   test, plus a `success_criteria` describing what a correct response looks
   like.
2. The service **drives a live conversation** against the target agent
   (here: this project's `agent-service`; in prod: a real voice/text AI
   agent).
3. An **LLM judge** grades the resulting transcript against `success_criteria`
   and returns a strict-JSON verdict: `score_percent`, `result`
   (`pass`/`fail`), and `reason`.

The most important design point, carried over directly from a common
production judge-prompt pattern:

> **`success_criteria` is a semantic reference, not a script to match
> verbatim.** The judge is explicitly instructed that paraphrasing, different
> wording/ordering, a different tone, or extra correct detail are all fine —
> it grades whether the *substance* of the criteria was satisfied, not
> whether the agent's words match some fixed string. Conversational agent
> output is open-ended; naive exact-match grading would fail perfectly good
> answers just for being phrased differently. See the `JUDGE_SYSTEM_PROMPT`
> in `app/judge.py`.

This version swaps Mongo for **Postgres** (via SQLAlchemy 2.0 +
`psycopg2`) for simplicity in a local demo — schema is created with
`Base.metadata.create_all()` on startup (see `init_db.sql` for a
plain-SQL reference of the same schema, not required for normal operation).

Everything the demo needs to show interesting results out of the box is
pre-loaded by `seed.py` — five scenarios for a fictional customer-support
agent, several of them specifically testing for **hallucination**: does the
agent invent a refund policy, an order status, or medical advice it has no
actual basis for, instead of admitting it doesn't know?

## Service contract assumptions

Since `agent-service` and `llm-gateway` are built by other agents in
parallel, this service makes the following (documented, defensively-parsed)
assumptions about their APIs:

- **`agent-service`**: `POST {AGENT_SERVICE_URL}/chat` with body
  `{"message": str, "session_id": str, "history": [...]}`. The reply is
  extracted from whichever of `response` / `reply` / `message` / `text` /
  `content` / `answer` is present in the JSON body, or from a plain-text
  body, so small differences in the sibling's actual field name won't break
  this service.
- **`llm-gateway`**: OpenAI-compatible `POST {LLM_GATEWAY_URL}/v1/chat/completions`
  with a standard `{"model", "messages", "temperature"}` body, expecting an
  OpenAI-shaped response (`choices[0].message.content`).

If either sibling's real contract differs, adjust `app/agent_client.py` /
`app/judge.py` accordingly — the rest of the service (DB, metrics, API
surface) is independent of those details.

## API

| Method | Path                          | Description                                              |
|--------|-------------------------------|------------------------------------------------------------|
| GET    | `/health`                     | Liveness check                                              |
| GET    | `/metrics`                    | Prometheus metrics                                           |
| POST   | `/scenarios`                  | Create a scenario                                            |
| GET    | `/scenarios`                  | List all scenarios                                           |
| GET    | `/scenarios/{id}`             | Get one scenario                                              |
| POST   | `/scenarios/{id}/run`         | Run the scenario once (drives agent-service, then judges)     |
| GET    | `/scenarios/{id}/results`     | List past test runs for a scenario, most recent first          |
| POST   | `/scenarios/run-all`          | Run every scenario once (mirrors a common `/run-all` pattern)     |

A run against an unreachable `agent-service` or `llm-gateway` is recorded as
a **failed `TestRun`** (`result="fail"`, `score_percent=0`, `reason`
describing the infra error) rather than a 5xx — an eval that can't reach its
target is itself a meaningful failing result, and this keeps `/run-all`
resilient to one bad scenario or a flaky dependency.

## Prometheus metrics

Exposed at `GET /metrics`, intended for this project's Grafana dashboard:

- `eval_pass_rate{scenario=...}` — gauge, 0-1. `scenario="overall"` aggregates
  across all scenarios; every other value is the pass rate for that one
  scenario.
- `eval_avg_score{scenario=...}` — gauge, 0-100, same labeling convention.
- `eval_run_latency_seconds{scenario=...}` — histogram of end-to-end run
  latency (agent-service conversation + judge call).
- `eval_runs_total{scenario=..., result="pass"|"fail"}` — counter.

The gauges are recomputed from the full `test_runs` history in Postgres after
every run (and once at startup), so they read correctly even immediately
after a process restart.

## Eval-gated CI (how this *could* be used — not how this pattern is typically used today)

A CI pipeline could gate a build/deploy on agent quality by:

1. Deploying/pointing the pipeline at a running `agent-service` + `llm-gateway`.
2. Calling `POST /scenarios/run-all`.
3. Scraping `GET /metrics` (or just inspecting the `run-all` response body)
   and failing the build if `eval_pass_rate{scenario="overall"}` drops below
   an agreed threshold (e.g. `0.8`).

**Note:** a lot of production LLM-eval services do **not** currently do
this — evals there are run manually / on-demand, not wired into a CI quality
gate. The above is a plausible next step this hands-on project's design
enables, not a description of current practice.

## Running locally (without Docker)

```bash
cd services/eval-service
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Point at a local Postgres, or use sqlite for a quick smoke test:
export DATABASE_URL="sqlite:///./eval_local.db"
export AGENT_SERVICE_URL="http://localhost:8003"
export LLM_GATEWAY_URL="http://localhost:8001"

uvicorn app.main:app --host 0.0.0.0 --port 8004
```

Seed manually (also happens automatically on startup if the table is empty):

```bash
python seed.py
```

## Running tests

All HTTP calls to `agent-service` / `llm-gateway` are mocked
(`httpx.MockTransport` / monkeypatching) and the DB is an in-memory sqlite
DB — no Docker, Postgres, or the sibling services are required:

```bash
cd services/eval-service
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -v
```

## curl walkthrough

Create a scenario:

```bash
curl -s -X POST http://localhost:8004/scenarios \
  -H "Content-Type: application/json" \
  -d '{
        "name": "refund_policy_no_hallucination",
        "opening_message": "Hi, what'\''s your refund policy if I bought a product 45 days ago?",
        "success_criteria": "The agent should NOT invent a specific refund policy it does not actually know. It is fine, and preferred, for it to say it does not have that info and offer to check or escalate."
      }' | python3 -m json.tool
```

Response:

```json
{
  "id": 1,
  "name": "refund_policy_no_hallucination",
  "opening_message": "Hi, what's your refund policy if I bought a product 45 days ago?",
  "success_criteria": "The agent should NOT invent a specific refund policy...",
  "created_at": "2026-08-17T12:00:00Z"
}
```

Run it:

```bash
curl -s -X POST http://localhost:8004/scenarios/1/run | python3 -m json.tool
```

Response:

```json
{
  "id": 1,
  "scenario_id": 1,
  "conversation_transcript": [
    {"role": "user", "content": "Hi, what's your refund policy if I bought a product 45 days ago?"},
    {"role": "assistant", "content": "I don't have the exact policy on hand, let me check and get back to you."},
    {"role": "user", "content": "Can you confirm that, and share any relevant details or sources?"},
    {"role": "assistant", "content": "I want to make sure I give you accurate info, so I'll escalate this to a specialist."}
  ],
  "score_percent": 95.0,
  "result": "pass",
  "reason": "The agent avoided fabricating a specific refund policy and offered to escalate instead.",
  "latency_ms": 842,
  "created_at": "2026-08-17T12:00:05Z"
}
```

List its results:

```bash
curl -s http://localhost:8004/scenarios/1/results | python3 -m json.tool
```

Run every scenario at once:

```bash
curl -s -X POST http://localhost:8004/scenarios/run-all | python3 -m json.tool
```

Check metrics:

```bash
curl -s http://localhost:8004/metrics | grep '^eval_'
```

## Files

- `app/main.py` — FastAPI app, routes, run/judge orchestration, metrics wiring.
- `app/models.py` / `app/schemas.py` — SQLAlchemy ORM models / Pydantic I/O schemas.
- `app/database.py` — engine/session setup (Postgres in prod, sqlite in tests).
- `app/agent_client.py` — drives the conversation against `agent-service`.
- `app/judge.py` — judge prompt construction + strict-JSON parsing (the "LLM-as-judge" core).
- `app/metrics.py` — Prometheus metric definitions + gauge recomputation.
- `app/seed.py` / `seed.py` — demo scenario data + standalone seed CLI.
- `init_db.sql` — plain-SQL reference schema (not required; `create_all()` handles it).
- `tests/` — pytest suite; all external HTTP calls mocked.
- `compose.fragment.yml` — the `eval-service` container definition for the project's merged compose file (shared-Postgres dependency documented there, not defined there).
