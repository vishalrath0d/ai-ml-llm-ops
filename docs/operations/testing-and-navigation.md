# Testing & navigating the running stack

Once `docker compose up -d --build` has finished (see `../../README.md` Quick Start), here's how to actually poke at it — every UI, every API doc, and a guided walkthrough in a sensible order.

## 0. Start here: the web UI

**http://localhost:8090** is an interactive control panel built specifically so you don't have to use curl for everything — Chat (talks to `agent-service`), Knowledge Base (ingest/query `rag-service`, naive vs. hybrid side by side), Evaluations (run scenarios against `eval-service`, see judge scores live), and Gateway & Chaos (raw `llm-gateway` testing + the latency/error-rate injection controls). See `../../services/web-ui/README.md` for how it's wired (plain HTML/JS, no build step, CORS-enabled on all 4 backends specifically to support it).

Everything below also works via curl/Swagger if you prefer — the web UI is a convenience layer on top of the same APIs, not a replacement for them.

## 1. Check everything is actually up

```bash
docker compose ps
```

Every row should say `running` or `healthy`. The Langfuse stack (6 containers) and ClickHouse are consistently the slowest to become healthy — give it 1-2 minutes after the containers first start. If something's stuck, `docker compose logs -f <service-name>` is your first move.

Then pull the local model once (only needed the first time, or after `docker compose down -v`):

```bash
docker compose exec ollama ollama pull qwen2.5:0.5b
```

## 2. Interactive API docs — every custom service is self-documenting

All four custom Python services are **FastAPI apps**, which means each one auto-generates a live, interactive OpenAPI (Swagger) UI — no separate documentation to maintain, no drift between docs and code. `feature-store` (Feast's own `feast serve`) is built on FastAPI too and gets the same UI for free. Open these directly in a browser:

| Service | Swagger UI | ReDoc (alt view) | Raw OpenAPI JSON |
|---|---|---|---|
| llm-gateway | http://localhost:8001/docs | http://localhost:8001/redoc | http://localhost:8001/openapi.json |
| rag-service | http://localhost:8002/docs | http://localhost:8002/redoc | http://localhost:8002/openapi.json |
| agent-service | http://localhost:8003/docs | http://localhost:8003/redoc | http://localhost:8003/openapi.json |
| eval-service | http://localhost:8004/docs | http://localhost:8004/redoc | http://localhost:8004/openapi.json |
| feature-store | http://localhost:6566/docs | http://localhost:6566/redoc | http://localhost:6566/openapi.json |

Swagger UI lets you **execute real requests from the browser** ("Try it out" button on each endpoint) — you don't need curl at all if you'd rather click through it.

## 3. Third-party UIs (the infra pieces)

| Tool | URL | First-time setup |
|---|---|---|
| Grafana | http://localhost:3001 | login `admin`/`admin`, change password when prompted. Dashboard **"AI Ops Overview"** is pre-provisioned — no setup needed to see it. |
| Langfuse | http://localhost:3000 | Part of the default stack, fully auto-provisioned — an org/project/API-keypair is created on first boot (see README's "Verified" section), so traces appear with zero signup or setup. Log in with the auto-created admin (`admin@aiops.local` / `localdev12345`) only if you want to click around the UI yourself. |
| MLflow | http://localhost:5050 | nothing to set up — it's empty until you run `train_and_log.py` (see exercise 6 below). |
| Prometheus | http://localhost:9090 | nothing to set up. Use the "Graph" tab to run any PromQL query listed in `../../services/observability/README.md` directly, before it's a Grafana panel. |
| Locust | http://localhost:8089 (only after you run it — see `../../load-testing/README.md`) | not part of `docker compose up`; run it separately per its README. |
| Langfuse MinIO console | http://localhost:9191 | only relevant if you need to inspect raw stored trace media; login `minio`/`sPSzqRzqorODt4IoKZnKcw` (placeholder dev creds, see `../../docker-compose.yml`). |

## 3b. Navigating MLflow & Langfuse, screen by screen — mapped to the exact code that produced each screen

The table above tells you the URL. This section tells you what you're actually looking at once you're in there, and which file in this repo produced it — so the UI stops being a black box and starts being a direct window into the code. Everything below is verified against a live run of this stack (real traces/runs exist, not a hypothetical).

### MLflow (http://localhost:5050)

MLflow has two top-level views, and it's easy to conflate them:

1. **Experiments** (left nav) — this is where individual *training runs* live. Open the experiment `train_and_log.py` created and you'll see two runs: `baseline-v1` and `candidate-v2-skewed-data`. Click into one:
   - **Parameters tab** — whatever was passed to `mlflow.log_param(...)` in `../../services/mlflow/train_and_log.py`.
   - **Metrics tab** — `accuracy`, logged via `mlflow.log_metric(...)` right after the held-out test set is scored. This is the number the script's own rollback logic compares between runs.
   - **Artifacts tab** — the actual serialized model (the `ColumnTransformer` + `LogisticRegression` pipeline, pickled via `mlflow.pyfunc.log_model(...)`). This is the exact file `agent-service` downloads and loads — click "MLmodel" here to see its raw metadata (Python version, flavor, signature).
2. **Models** (top nav) — this is the **registry**, a separate concept from experiments. Open `support-urgency-classifier` and you'll see:
   - **Versions list** — v1 (baseline), v2 (the deliberately-worse candidate trained on skewed data).
   - **Aliases tab** — this is the one that matters operationally. `champion` points at whichever version is "live." `train_and_log.py` moves this pointer with `MlflowClient.set_registered_model_alias(...)` — promote it to v2, detect the regression, roll it back to v1, all visible here as a plain history of one alias being reassigned.

**How this UI connects to a running request**: `../../services/agent-service/app/model_registry.py` polls this exact `champion` alias (`get_model_version_by_alias`), downloads whatever version it points to, and caches it in memory. `GET /admin/model-status` on agent-service (localhost:8003) tells you which version is *currently loaded in the running process* — compare that against what the Aliases tab says is *currently registered* to see the gap between "promoted" and "actually serving traffic" (they sync within agent-service's refresh window, or immediately via `POST /admin/reload-model`). This is the same "did my promotion actually reach production" question a real MLOps dashboard exists to answer.

### Langfuse (http://localhost:3000)

Langfuse's home view is a **Traces** table — one row per traced unit of work. Two different trace names show up, and they come from two different services' `app/tracing.py`:

- **`agent-service.chat`** — one root trace per `/chat` request, started in `../../services/agent-service/app/tracing.py`'s `traced_chat_turn(...)` context manager (see `app/main.py`'s `/chat` handler for where it's opened).
- **`chat-completion`** — a *separate* trace opened by `../../services/llm-gateway/app/tracing.py` (see `app/main.py:148`) every time llm-gateway itself handles a completion request — including calls that didn't come from agent-service (e.g. testing llm-gateway directly via Swagger or the web UI's "Gateway & Chaos" tab).

Click into any trace to get the **span tree** — the actual call graph for that one turn, in order, with input/output captured at each step:

- Inside an `agent-service.chat` trace, nested spans/generations show the downstream call into llm-gateway, and (if the message triggered a tool call) a span for `crm_lookup` — see `../../services/agent-service/app/tools_impl.py` and `app/mcp_server.py` for what that tool call actually does.
- Inside a `chat-completion` trace, the nested span is named after whichever provider handled it (`../../services/llm-gateway/app/main.py`'s `provider_name` / `chain[0]` — e.g. `ollama`), so a multi-provider fallback chain is visible as multiple attempted child spans, not just one.

**Scores tab** on a trace: this is where `../../services/agent-service/app/online_eval.py` writes to — a sampled (`ONLINE_EVAL_SAMPLE_RATE`), asynchronous LLM-judge score, attached after the fact via `Langfuse.create_score(trace_id=...)`. Read the module docstring in `online_eval.py` before assuming this is the only quality signal in the system — it's explicitly *not* the same thing as `eval-service`'s offline scenario grading (different system, different trigger, see the root README's "How it all works" §3 for the three-way comparison against guardrails too), and it's also not what decided pass/fail for anything — it's a production quality signal riding alongside a trace a human would already be looking at.

**What you will *not* find a Langfuse score for**: a guardrail firing. `../../services/llm-gateway/app/guardrails.py`'s checks are deliberately cheap regex/keyword rules that run inline, in the critical path, on every request — fast enough to block or redact before a response goes out, but they don't call an LLM and don't write a Langfuse score. If you want to see a guardrail actually fire, use the web UI's "Gateway & Chaos" tab or POST a message containing something like a fake credit-card number directly to llm-gateway and diff the request/response — the trace will show the completion happened, but the redaction itself is a guardrails.py code path, not a Langfuse artifact.

**Verifying the wiring yourself, without trusting this doc**: Langfuse's actual trace/span/score data lives in ClickHouse, not Postgres (Postgres only holds org/project/API-key metadata) — so if traces aren't showing in the UI, checking Postgres will mislead you into thinking nothing is captured. Check the real store directly:
```bash
docker exec langfuse-clickhouse clickhouse-client --query "SELECT count() FROM default.traces"
docker exec langfuse-clickhouse clickhouse-client --query "SELECT count() FROM default.observations"
docker exec langfuse-clickhouse clickhouse-client --query "SELECT count() FROM default.scores"
docker exec langfuse-clickhouse clickhouse-client --query "SELECT name, timestamp FROM default.traces ORDER BY timestamp DESC LIMIT 5 FORMAT TSV"
```

## 3c. Is the stack actually integrated, or just running side by side? — a verification recipe

"All 17 containers show `Up`" proves nothing about whether they're wired *to each other*. Run these to actually confirm integration, not just liveness (all four were run against this exact stack while writing this doc):

```bash
# 1. Prometheus is scraping real /metrics endpoints, not just up itself
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import json,sys
d = json.load(sys.stdin)
for t in d['data']['activeTargets']:
    print(t['labels'].get('job'), '->', t['health'], t['scrapeUrl'])
"
# expect: agent-service, eval-service, llm-gateway, rag-service, prometheus — all "up"

# 2. Grafana can reach Prometheus and its dashboard is provisioned (not manually built)
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3001   # 302 = healthy (redirect to login)
cat services/observability/grafana/dashboards/aiops-overview.json | python3 -c "
import json,sys; d=json.load(sys.stdin)
print(d['title']); [print('-', p.get('title')) for p in d['panels']]
"

# 3. Langfuse is actually receiving traces from live traffic (see 3b above for why
#    ClickHouse, not Postgres, is the real answer here)
docker exec langfuse-clickhouse clickhouse-client --query "SELECT count() FROM default.traces"

# 4. MLflow's registry alias and agent-service's loaded model actually agree
curl -s http://localhost:8003/admin/model-status
# compare "version" here against the Aliases tab for support-urgency-classifier in the MLflow UI
```

If any of these come back empty/zero on a fresh stack, that's expected until you've sent at least one `/chat` request and run `../../services/mlflow/run_training.sh` once — see section 4 below for exactly that walkthrough. If they're still empty *after* that, something is actually disconnected and worth debugging via `docker compose logs -f <service>` before trusting anything the UI shows.

## 4. Guided walkthrough — hit every service once, in order

This exercises the whole stack end to end and is the fastest way to convince yourself it's actually wired together correctly.

```bash
# 1. llm-gateway: raw chat completion, no other services involved
curl -s http://localhost:8001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"Say hello in five words."}]}' | python3 -m json.tool

# 2. rag-service: ingest a doc, then query it two ways
curl -s -X POST http://localhost:8002/ingest \
  -H 'Content-Type: application/json' \
  -d '{"text":"Onwly Pro users can reset their password from Settings > Security > Reset Password.","source":"manual-test"}'

curl -s -X POST http://localhost:8002/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"how do I reset my password","mode":"naive"}' | python3 -m json.tool

curl -s -X POST http://localhost:8002/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"how do I reset my password","mode":"hybrid"}' | python3 -m json.tool
# compare "retrieval_path" in the two responses

# 3. agent-service: a tool-calling conversation (watch it call crm_lookup)
curl -s -X POST http://localhost:8003/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"demo-1","message":"look up the customer jane@example.com"}' | python3 -m json.tool

# 4. eval-service: run the seeded scenarios through agent-service and judge them
curl -s -X POST http://localhost:8004/scenarios/run-all | python3 -m json.tool
curl -s http://localhost:8004/metrics | grep eval_
```

Then:
5. Open **Grafana** → "AI Ops Overview" and confirm the Traffic/Latency panels moved after step 4's load, and the two Eval Quality gauges show real numbers.
6. If you set up Langfuse (step 3 in the table above) and re-ran step 1 or 3, open **Langfuse** → Traces and find the request you just made — click into it to see the full prompt/completion/token breakdown.
7. Inject chaos and repeat: `curl -X POST http://localhost:8001/admin/chaos -d '{"latency_ms":2000,"error_rate":0.3}'`, then repeat step 3, and watch Grafana's latency panel and (if enabled) the Langfuse trace both reflect it. Reset with `latency_ms: 0, error_rate: 0.0`.

## 5. The exercises NOT reachable via the running containers

A few things in this project are standalone scripts, not always-on services — they're not part of `docker compose up`, run them separately per their own README:

- `../../services/mlflow/train_and_log.py` — baseline/rollback demo (needs `docker compose up mlflow postgres` running first).
- `../../services/feature-store/feature_repo/demo.py` — Feast train/serve-skew demo (fully self-contained, no docker needed).
- `../../services/llm-gateway/quantization_demo.py` — fp32 vs 8-bit memory comparison (fully self-contained, no docker needed).
- `../../load-testing/locustfile.py` — needs the stack already running (`locust -f locustfile.py --host http://localhost:8003`).
- `../../sre/blue_green_demo.sh` — fully self-contained toy demo, no dependency on the rest of the stack.

## 6. Troubleshooting quick reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `llm-gateway` chat call hangs or 500s | Ollama model not pulled yet | `docker compose exec ollama ollama pull qwen2.5:0.5b` |
| `eval-service` won't start | Postgres not ready yet / `eval_db` missing | `docker compose logs postgres` — confirm `../../postgres-init/01-init-databases.sql` ran; restart with `docker compose restart eval-service` once postgres is healthy |
| Port already in use on `up` | Something else on your machine already bound that port | Check `../../docker-compose.yml`'s comments — `mlflow` and the Langfuse MinIO ports were already moved once during integration for exactly this reason (macOS AirPlay on 5000, a Prometheus/MinIO clash on 9090) |
| Grafana panels all show "No data" | Prometheus hasn't scraped yet, or the app services aren't running | Wait 15-30s (scrape interval), check http://localhost:9090/targets — every target should show `UP` |
| Ollama model load fails / `llama-server process has terminated: signal: killed` in `docker compose logs ollama` | Docker Desktop's VM ran out of memory | Docker Desktop → Settings → Resources → bump Memory to at least 5GB, then restart Docker Desktop. The full 17-container default stack (Langfuse included) runs at ~3.6GB under load in this project's own testing at 5GB allocated — 4GB (Docker's own default) isn't enough on an 8GB host. |
| `llm-gateway`/`agent-service`/`eval-service` calls feel slow | CPU-only Ollama inference — post-fix (see README's "Verified" section) this is ~18-25s per call on constrained hardware, not the 48-165s seen before the memory fixes | Still use a generous `curl -m 120`+ client-side timeout to be safe. `agent-service`'s `LLM_REQUEST_TIMEOUT_SECONDS` (180s) and `eval-service`'s `HTTP_TIMEOUT_SECONDS` (240s) already have headroom built in. For a snappier feel, set a real GEMINI_API_KEY/OPENAI_API_KEY/ANTHROPIC_API_KEY in `.env` -- `LLM_PROVIDER_CHAIN` tries them before ever reaching Ollama. |
| `agent-service` doesn't call `crm_lookup` even when you explicitly ask it to look something up | The free `qwen2.5:0.5b` model is weak at tool-calling reasoning — this is a model-quality limitation, not a wiring bug (confirmed: the same model handles plain conversational turns fine) | Set a real GEMINI_API_KEY/OPENAI_API_KEY/ANTHROPIC_API_KEY in `.env` for reliable tool-calling behavior. |
| Memory feels tight with the full stack (Langfuse included) on an 8GB machine | Langfuse's 7-container stack (own Postgres+ClickHouse+Redis+MinIO+web+worker) is the heaviest piece, ~3.5GB combined under load — it caused real cascading failures earlier in this project before other memory fixes (mlflow, rag-service) freed up headroom; re-verified stable since, but still the thing to drop first if you're constrained | If you need a lighter footprint, bring up everything except the langfuse-* services (see README's Quick Start for the explicit service list), or just give Docker Desktop more memory (Settings → Resources). Langfuse self-heals via `restart: unless-stopped` regardless. |
| Wondering what's actually using memory | Want ground truth, not guesses | `docker stats --no-stream` — post-fix, the default 11-service stack idles around ~1.4GB total (verified). If something looks unexpectedly large, that's worth investigating the same way `mlflow`'s 1.6GB idle footprint and `rag-service`'s bloated image were tracked down and fixed this session (see README). |

## 7. Testing by discipline — AIOps, MLOps, LLMOps, whole-system vs. per-service

the root README's "Architecture" section, "what maps to which Ops discipline" table, names the pieces; this section is how to actually exercise each one, at two altitudes: **whole-system** (an exercise that proves several services are correctly wired together) and **per-service** (isolate one piece and test only it, the way you'd actually debug a real regression — you don't re-test the whole stack to find out which one service broke).

### LLMOps — `llm-gateway`, `agent-service`, Langfuse

**Whole-system test:** send a chat through the web UI (or `curl agent-service /chat`) and confirm the full chain worked: `agent-service` returned a response, `llm-gateway`'s `/metrics` shows an incremented `llm_gateway_requests_total{status="success"}`, and a matching trace appears in Langfuse (http://localhost:3000, on by default) with the full prompt/completion/token breakdown.
```bash
curl -X POST localhost:8003/chat -H 'Content-Type: application/json' -d '{"session_id":"llmops-1","message":"hello"}'
curl -s localhost:8001/metrics | grep llm_gateway_requests_total
```

**Per-service tests:**
- **`llm-gateway` in isolation** — test the gateway without going through the agent at all: `curl -X POST localhost:8001/v1/chat/completions -d '{"model":"qwen2.5:0.5b","messages":[{"role":"user","content":"hi"}]}'`. Toggle `LLM_PROVIDER_CHAIN` in `.env` + `docker compose up -d llm-gateway` to test the N-deep fallback chain directly — kill Ollama (`docker compose stop ollama`) with `LLM_PROVIDER_CHAIN=ollama,anthropic` (or any chain with a configured fallback) and confirm the gateway still responds via the next provider instead of 502ing.
- **`agent-service` in isolation** — its own test suite mocks `llm-gateway` entirely: `cd services/agent-service && pip install -r requirements.txt -r requirements-dev.txt && pytest` — this is how you test the LangGraph routing/loop-guard logic itself without depending on a live model or network call at all.
- **MCP surface test** — `agent-service` is also an MCP server; point `npx @modelcontextprotocol/inspector` at `http://localhost:8003/mcp` to inspect the exact tool schemas an MCP client would see, independent of the `/chat` REST path.
- **Provider-swap regression check** — the real LLMOps question "if we switch providers, does behavior stay consistent?" Run the same `/chat` message once with `LLM_PROVIDER_CHAIN=ollama` and once with a real key set (`LLM_PROVIDER_CHAIN=openai` + `OPENAI_API_KEY`), diff the `tool_calls` arrays — this is exactly the tool-calling-reliability gap documented in the README's "Verified" section, made concrete.

### MLOps — `mlflow`, `agent-service`'s live registry consultation, `feature-store`

**Whole-system test:** `run_training.sh` end to end proves `mlflow` ↔ `postgres` (mlflow_db) ↔ the artifact volume ↔ `agent-service` are ALL wired correctly — not just the registry in isolation. It registers two model versions, aliases one `champion`, "promotes" a deliberately worse one, detects the regression via held-out accuracy, and rolls back — then you confirm the SAME message gets classified differently by `agent-service` depending on which version was champion at the time:
```bash
cd services/mlflow && ./run_training.sh    # NOT a plain pip install + python — see README.md for why

curl -X POST localhost:8003/admin/reload-model
curl -X POST localhost:8003/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"t1","message":"This is the third time your product has broken and I am furious, fix it right now!"}'
# -> "urgency":"urgent" (the script leaves champion on the GOOD version when it finishes)
```
Then open http://localhost:5050 and confirm both versions and the alias history are visible in the registry UI — that's the whole-system check (script + server + DB + UI + live service all agreeing).

**Per-service tests:**
- **`mlflow` in isolation** — hit its REST API directly without the training script at all: `curl localhost:5050/api/2.0/mlflow/experiments/search -X POST -H 'Content-Type: application/json' -d '{}'` — confirms the server+DB connection independent of any client-side Python code.
- **`agent-service`'s registry consultation in isolation** — `curl localhost:8003/admin/model-status` shows what's loaded right now without touching MLflow at all; `POST /admin/reload-model` forces a fresh pull. If `loaded_version` is `null`, either MLflow isn't reachable or `run_training.sh` hasn't been run yet — both degrade gracefully (urgency classification just returns `"unknown"`), confirmed not to break `/chat`.
- **`feature-store` live, as `agent-service` actually uses it** — no longer just a standalone lesson: `curl -X POST localhost:6566/get-online-features -H 'Content-Type: application/json' -d '{"features":["customer_engagement_features:engagement_score","customer_engagement_features:days_since_last_contact","customer_engagement_features:total_conversations","customer_engagement_features:open_tickets"],"entities":{"customer_id":["cust_003"]}}'` hits the same endpoint `app/feature_store_client.py` calls on every `/chat` turn a `customer_id` is given. The real proof it's wired in: same borderline message, two different customers, two different urgency labels —
  ```bash
  curl -X POST localhost:8003/chat -H 'Content-Type: application/json' \
    -d '{"session_id":"f1","message":"Any update on the issue I reported earlier?","customer_id":"cust_002"}'
  # -> "urgency":"normal" (cust_002: healthy, 0 open tickets)
  curl -X POST localhost:8003/chat -H 'Content-Type: application/json' \
    -d '{"session_id":"f2","message":"Any update on the issue I reported earlier?","customer_id":"cust_003"}'
  # -> "urgency":"urgent" (cust_003: at-risk, 3 open tickets) -- same text, different customer, different answer
  ```
  See `../../services/feature-store/README.md`'s "Live integration" section.
- **`feature-store`'s train/serve-skew lesson, standalone** — this part is still fully self-contained, no Docker dependency at all: `cd services/feature-store/feature_repo && pip install -r ../requirements.txt && python demo.py`. The thing to actually check in the output: does the offline (training-time) value for a customer match the online (serving-time) value for the same customer at the same point in time? A mismatch in that specific comparison is what "train/serve skew" looks like concretely.
- **Baseline/rollback drill, the literal mechanic** — after running `run_training.sh` once, flip `champion` to the OTHER (bad) version directly (`MlflowClient(tracking_uri=...).set_registered_model_alias(...)`, see `../../services/mlflow/README.md` for the exact snippet), reload, and repeat the SAME chat message — watch `urgency` change from `"urgent"` to `"normal"`. That state flip, live, is "how do I revert" answered concretely instead of hypothetically.


### AIOps (ops-for-this-AI-system — not to be confused with AI-for-ops; see `../concepts/02-llmops-mlops-tooling.md` §1) — `eval-service`, `prometheus`, `grafana`, `ci-cd/`, `sre/`

**Whole-system test:** the eval-gate script in `../../ci-cd/github-actions-ci.yml` is the canonical whole-system AIOps check — it brings up the stack, runs every service's own test suite, then calls `eval-service`'s `/scenarios/run-all` and fails the build if `eval_pass_rate` drops below threshold. Run its logic locally without GitHub Actions:
```bash
curl -X POST localhost:8004/scenarios/run-all
curl -s localhost:8004/metrics | grep 'eval_pass_rate{scenario="overall"}'
# then compare the printed value against your threshold by hand, or see
# ci-cd/github-actions-ci.yml for the exact scripted version of this check
```

**Per-service tests:**
- **`eval-service` in isolation** — create and run a scenario without touching the CI script at all: `curl -X POST localhost:8004/scenarios -d '{"name":"test","opening_message":"hi","success_criteria":"agent should be polite"}'` then `curl -X POST localhost:8004/scenarios/{id}/run`. Its own `pytest` suite (36 tests) mocks both `agent-service` and `llm-gateway` entirely — `cd services/eval-service && pytest` — for testing the judge-prompt/JSON-parsing logic in total isolation.
- **`prometheus` in isolation** — bypass Grafana entirely and query raw metrics: open http://localhost:9090/graph and run `histogram_quantile(0.95, sum(rate(agent_service_chat_latency_seconds_bucket[5m])) by (le))` directly. Check http://localhost:9090/targets to confirm every scrape target shows `UP` before assuming a dashboard problem is a data problem.
- **`grafana` in isolation** — confirm provisioning worked without touching any app service: the "AI Ops Overview" dashboard should already exist under Dashboards on a fresh `docker compose up`, with Prometheus already wired as a datasource — if either is missing, the problem is in `../../services/observability/grafana/provisioning/`, not in the metrics themselves.
- **SRE drill** — inject chaos in isolation (`curl -X POST localhost:8001/admin/chaos -d '{"latency_ms":3000,"error_rate":0.2}'`), then watch three things independently: Grafana's latency panel (metrics), a Langfuse trace if enabled (tracing), and how long it takes YOU to notice something's wrong (that's a manual MTTD measurement — see `sre-practices.md#mttd-and-mttr-tracking` for the template to log it in). Reset with `latency_ms:0, error_rate:0.0` when done. Separately, `../../sre/README.md` (and `../../sre/blue_green_demo.sh`) is fully self-contained and tests zero-downtime cutover mechanics without touching any of the AI services at all.

### Quick regression sweep — "did I break anything?"

The fastest whole-system health check, reusable after any change (this is exactly what was run to verify every fix in this session):
```bash
for url in localhost:8001/health localhost:8002/health localhost:8003/health localhost:8004/health localhost:8090/; do
  curl -s -o /dev/null -w "$url -> %{http_code}\n" -m 5 "$url"
done
docker compose logs --tail 100 | grep -iE "error|exception|traceback|fatal"
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"
```
All five URLs should return `200`, the log grep should return nothing (or only clearly-labeled harmless noise — check `../../docker-compose.yml`'s comments if something new shows up), and no single container's memory should be wildly out of line with what `../../README.md`'s "Verified" section documents as normal.
