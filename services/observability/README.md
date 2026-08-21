# Observability — Prometheus + Grafana (the metrics gap-filler)

## Why this exists

It's common for an org's only visibility into running services to be
**centralized logs**. That answers "what happened in this one request" but not "how is
the system behaving right now" — no request-rate graphs, no latency
percentiles, no error-rate trend, no way to see a quality metric (like an
eval pass rate) move over time without grepping logs. This folder is the
concrete fix: a real metrics pipeline (scrape -> store -> dashboard) for
the four app services in this project, all of which already expose
Prometheus-format metrics at `/metrics` per the shared instrumentation
convention:

- `llm-gateway:8001`
- `rag-service:8002`
- `agent-service:8003`
- `eval-service:8004`

## Files

- `prometheus.yml` — scrape config, 15s interval, targets by docker-compose
  service name.
- `compose.fragment.yml` — `prometheus` (port 9090) and `grafana` (port
  **3001**, not 3000 — 3000 is used by `langfuse-web`, see
  `../langfuse/compose.fragment.yml`).
- `grafana/provisioning/datasources/prometheus.yml` — auto-registers
  Prometheus as a Grafana datasource (uid `prometheus`) on startup, so you
  don't have to click through the UI to add it.
- `grafana/provisioning/dashboards/dashboard.yml` — tells Grafana to load
  any dashboard JSON dropped into `grafana/dashboards/`.
- `grafana/dashboards/aiops-overview.json` — the actual dashboard.

## Metric names: verified against real code, not guessed

The other services in this project are being built by other agents in
parallel. Partway through building this dashboard, `llm-gateway`,
`rag-service`, and `agent-service` had already landed real
`app/metrics.py` files, so the queries below were checked directly against
that code rather than assumed. **Important finding: there is no shared
`http_requests_total`-style convention across services** — each service
picked its own metric names/namespace. That's why every panel below uses
one PromQL query *per service* (not a single grouped-by-`service` query)
with the service name baked into each query string.

Metric names confirmed by reading each service's `app/metrics.py` /
`app/main.py` at the time this was written:

| Service | Request counter | Labels/values seen in code | Latency histogram |
|---|---|---|---|
| `llm-gateway` | `llm_gateway_requests_total` | `status`: `success` \| `error` \| `chaos_error` | `llm_gateway_request_latency_seconds` |
| `rag-service` | `rag_query_total` | `mode`, `retrieval_path` — **no status/outcome label** | `rag_query_latency_seconds` |
| `agent-service` | `agent_service_chat_requests_total` | `status`: `ok` \| `error` | `agent_service_chat_latency_seconds` |
| `eval-service` | `eval_runs_total` | `scenario`, `result`: `pass` \| `fail` | `eval_run_latency_seconds` (labeled `scenario`) |

Concrete consequences reflected in `aiops-overview.json`:

- **Error Rate panel excludes rag-service.** `rag_query_total` has no
  success/error label today, so an error ratio can't be derived from it.
  This is a real, worth-fixing instrumentation gap, not an oversight in
  this dashboard — flagging it here is more useful than quietly working
  around it.
- **eval-service's "Request Rate"/"Error Rate"/latency panels use
  `eval_runs_total` and `eval_run_latency_seconds`** — these were
  originally written against assumed names (`eval_service_requests_total`
  / `eval_service_request_latency_seconds`) before eval-service was
  finished, and were corrected once its real `app/metrics.py` landed. Its
  "error rate" panel uses `eval_runs_total{result="fail"}` as the closest
  available proxy — a failed eval run isn't a service error in the HTTP
  sense, just the best available "something's wrong" signal for that
  service.
- **The two eval-quality gauges filter to `{scenario="overall"}`** —
  `eval_pass_rate` and `eval_avg_score` are both labeled per-scenario plus
  an `overall` aggregate; the gauges show the aggregate. `eval_avg_score`
  is displayed with Grafana's `percent` unit (raw value already 0–100),
  not `percentunit` (which expects 0–1) — that mismatch was caught and
  fixed during integration.

## Running it

1. As part of the merged root compose (once the integration step wires all
   fragments together): the 4 app services + `prometheus` + `grafana` all
   need to be on the same docker network, with `prometheus.yml`'s targets
   matching the actual service names used in the merged compose file.
2. Open **http://localhost:9090** to confirm Prometheus is scraping —
   check **Status -> Targets**; all 4 app services (plus Prometheus
   itself) should show as `UP`. If a target is `DOWN`, that service either
   isn't running or isn't exposing `/metrics` on the expected port yet.
3. Open **http://localhost:3001** for Grafana. Log in with
   `admin` / `admin` (set via `GF_SECURITY_ADMIN_PASSWORD` in
   `compose.fragment.yml`) — **change this on first login**, Grafana will
   prompt you to.
4. The **AI Ops Overview** dashboard should already be there (folder
   "AI Ops") thanks to the provisioning config — no manual import needed.

## What each panel shows, and the PromQL behind it

Learning PromQL by reading working queries beats clicking around a UI, so
here's every panel's query explicitly:

| Panel | Query | What it tells you |
|---|---|---|
| Request Rate by Service | `sum(rate(http_requests_total[5m])) by (service)` | Requests/sec per service, averaged over a trailing 5-minute window. `rate()` on a Counter gives you a per-second rate even though the underlying metric only ever increases. |
| Error Rate by Service | `sum(rate(http_requests_total{status_code=~"5.."}[5m])) by (service) / sum(rate(http_requests_total[5m])) by (service)` | Fraction of requests returning 5xx, per service. The `{status_code=~"5.."}` regex filters to server errors only; dividing by total request rate turns an absolute count into a ratio (0-1, shown as %). |
| Request Latency P50 | `histogram_quantile(0.50, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))` | Median request latency per service. `histogram_quantile` needs the rate of each histogram bucket (hence `sum(rate(..._bucket[5m])) by (le, service)` — `le` must be preserved in the `by` clause or the quantile math breaks). |
| Request Latency P95 | `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, service))` | Same as above at the 95th percentile — this is usually the more actionable number since it reflects your worst "typical" user experience, not the average case. |
| Eval Pass Rate | `eval_pass_rate` | Raw gauge value straight from eval-service — no rate/aggregation needed since it's already a point-in-time ratio. |
| Eval Avg Score | `eval_avg_score` | Same idea — a raw gauge, plotted directly. |
| Service Up | `up{job=~"llm-gateway|rag-service|agent-service|eval-service"}` | `up` is a metric Prometheus generates itself for every scrape target: `1` if the last scrape succeeded, `0` if it didn't. This is the fastest way to answer "is this service even reachable" independent of anything the service itself reports. |

## What to try next

- Add a panel for tokens/cost if `llm-gateway` exposes a
  `llm_tokens_total` or similar counter — same `rate()` + `sum by` pattern
  as request rate.
- Add alerting rules on top of the error-rate and eval-pass-rate queries
  above once you're ready to move past "look at a dashboard" into
  "get paged when eval quality drops."
