# web-ui

The interactive control panel this project was missing: a single page to actually *use* the stack instead of only curl/Swagger. Plain HTML/CSS/vanilla JS, served by nginx — no npm install, no build step, no framework, kept deliberately light given this project already pushes an 8GB-RAM dev machine to its limit.

## What it mirrors

A lot of real production setups split this across two separate frontends: one for agent config/chat testing, one for eval dashboards. This project combines both roles into one page since it's a single small hands-on system, not a multi-team product suite.

## Tabs

- **Chat** — talks to `agent-service` (`POST /chat`). Shows the conversation and which tools fired each turn.
- **Knowledge Base** — talks to `rag-service`. Ingest text, then query it in `naive` vs `hybrid` mode side by side.
- **Evaluations** — talks to `eval-service`. Lists the seeded scenarios, runs one (or all) against `agent-service`, shows the judge's pass/fail + score + reason, and a live pull of the `eval_*` Prometheus metrics.
- **Gateway & Chaos** — talks directly to `llm-gateway`. Raw chat-completion testing bypassing the agent, plus the `/admin/chaos` latency/error-rate controls.
- **Links** — Grafana/Langfuse/MLflow/Prometheus and every service's own Swagger UI. Not rebuilt here on purpose — they already have UIs.

## How it talks to the backend

The browser calls `llm-gateway`/`rag-service`/`agent-service`/`eval-service` **directly** on their host-mapped ports (`localhost:8001-8004`), not through a proxy. That only works because CORS (`allow_origins=["*"]`) was added to all four services specifically to support this UI — see the `app.add_middleware(CORSMiddleware, ...)` block in each service's `app/main.py`, clearly commented as a local-hands-on-project-only choice (a real deployment would scope `allow_origins` to the actual UI's domain, not `*`).

Base URLs are in `config.js` — edit them if you've remapped any host ports.

## Why responses can take a long time

CPU-only local inference (whenever the fallback chain reaches `ollama`, its last entry by default) genuinely takes 30-180+ seconds per call on modest hardware — observed directly during this project's own verification run. The UI shows an elapsed-time counter while waiting rather than a fixed timeout, because there's no client-side timeout set by default (see `config.js`'s `REQUEST_TIMEOUT_MS`). For a snappier experience, set a real `GEMINI_API_KEY`/`OPENAI_API_KEY`/`ANTHROPIC_API_KEY` in the root `.env` so the chain succeeds on one of the hosted providers before ever reaching Ollama.

## Running it standalone

```bash
docker build -t web-ui .
docker run -p 8090:80 web-ui
# open http://localhost:8090 -- the backend services still need to be running
# separately (docker compose up) for anything to actually work.
```
