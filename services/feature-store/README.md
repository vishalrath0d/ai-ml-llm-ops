# feature-store

A minimal, local/file-based [Feast](https://feast.dev) feature store —
**a lot of orgs have no feature store**, so this started as a hands-on
"here's what one looks like and what it would solve" teaching example. It's
since become more than that: `agent-service`'s urgency classifier is a
**real, live consumer** of this feature view's online store, not just a
standalone lesson — see "Live integration" below. The offline
train/serve-skew walkthrough (`python demo.py`) still exists too, as a
focused lesson on that one concept in isolation.

## Live integration: a real input to agent-service's urgency classifier

This service runs as `feature-store` in `docker-compose.yml`, serving
Feast's online store over HTTP via `feast serve` (the same "every
capability is its own service" pattern `rag-service`/`llm-gateway` already
use, rather than `agent-service` importing `feast` in-process). On every
`POST /chat` where a `customer_id` is given, `agent-service` calls this
service's `POST /get-online-features` and feeds the result — alongside the
message text — into the urgency classifier (see
`../mlflow/train_and_log.py` and
`../agent-service/app/feature_store_client.py`).

**Try it** (with the stack running):

```bash
# Same borderline message, two different customers -- healthy vs at-risk
curl -s http://localhost:8003/chat -H "Content-Type: application/json" \
  -d '{"session_id":"demo-1","message":"Any update on the issue I reported earlier?","customer_id":"cust_002"}' \
  | python -m json.tool   # cust_002: healthy (engagement 0.93, 0 open tickets) -> "normal"

curl -s http://localhost:8003/chat -H "Content-Type: application/json" \
  -d '{"session_id":"demo-2","message":"Any update on the issue I reported earlier?","customer_id":"cust_003"}' \
  | python -m json.tool   # cust_003: at-risk (engagement 0.18, 3 open tickets) -> "urgent"
```

Same text, same model, different customer, different answer — that's the
concrete proof this is a real dependency, not decoration. `cust_001`/`002`/`003`
are hand-set in `feature_repo/generate_data.py` to match the same three
customers `agent-service`'s mocked CRM already knows (`app/tools_impl.py`) —
Jane Doe, Amit Shah, and Maria Gomez respectively — so the two fake datasets
tell one coherent story instead of two disconnected ones. The other 17
customers (`cust_004`..`cust_020`) still get randomly generated features.

You can also query the online store directly, the same way `agent-service`
does:

```bash
curl -s -X POST http://localhost:6566/get-online-features -H "Content-Type: application/json" -d '{
  "features": [
    "customer_engagement_features:engagement_score",
    "customer_engagement_features:days_since_last_contact",
    "customer_engagement_features:total_conversations",
    "customer_engagement_features:open_tickets"
  ],
  "entities": {"customer_id": ["cust_003"]}
}' | python -m json.tool
```

No `customer_id` given, the service unreachable, or an unknown customer_id
all degrade to neutral default features (`app/feature_store_client.py`'s
`DEFAULT_FEATURES`) rather than blocking `/chat` or skewing the
classification toward either label — same defensive posture as MLflow and
Langfuse elsewhere in this project.

**Registry/online store are built once, at image build time** (see
`Dockerfile`), not at container startup — this is Feast's local/file-based
provider serving a fixed, deterministic fake dataset that never changes at
runtime, so there's no reason to redo `feast apply`/`materialize` on every
restart. If you regenerate the fake dataset (`generate_data.py`) or add a
feature, rebuild the image (`docker compose build feature-store`) to bake
in the new data.

## Why this exists — the concrete problem this addresses

A production feature-serving layer already computes per-conversation signals like
**engagement_score**, **days_since_last_contact**, and
**total_conversations** for the conversational-AI product. Today, those
numbers live and die inside that one service — there is no supported way
for another model or service to reuse them.

Say someone wants to build a **churn-prediction model** next quarter. Two
things have to happen:

1. **Training**: pull historical values of those same signals, as they were
   known at the time each historical outcome (churned / didn't churn) was
   recorded, to build a training set.
2. **Serving**: at prediction time, look up each customer's *current*
   values of those same signals, fast (low-latency, since it's on a
   request path), and feed them into the model.

Without a feature store, the churn team's only real option is to
**reimplement** `engagement_score` — read the same underlying event data
and recompute it, in their own training pipeline and again in their own
serving code. Reimplementation is where things quietly go wrong.

### What "train/serve skew" concretely means here

**Train/serve skew** is when the *same conceptual feature* is computed two
different ways in the training path versus the serving path, so the model
sees different numbers for "the same thing" depending on which path
produced them — even though nobody intended that.

Concretely, imagine the churn team's training pipeline computes
`engagement_score` with a batch SQL job that looks at the **last 30 days**
of activity, while a production feature-serving layer's real-time serving code
(reimplemented independently, maybe by a different engineer, maybe months
later) computes what it also calls `engagement_score` from a **rolling
7-day window**, or handles a customer with zero conversations as `NULL`
in one path and `0.0` in the other. The model was trained on 30-day-window
values but is served 7-day-window values in production. Nothing crashes —
there's no error to catch — the model just quietly makes worse predictions
than it did in evaluation, because at inference time it's looking at inputs
that don't statistically resemble what it learned from. This class of bug
is notoriously hard to catch because both pipelines look correct in
isolation; the mismatch only shows up in a puzzling live-vs-eval accuracy
gap.

### How a feature store actually prevents this

A feature store's core trick is: **define the feature once**, and make
both the training path and the serving path read through that *one*
definition.

- [`feature_repo/features.py`](./feature_repo/features.py) defines a single
  `FeatureView` (`customer_engagement_features`) over one data source.
- `store.get_historical_features(...)` — used for **training** — does a
  point-in-time-correct join against that source: for each training row's
  timestamp, it returns the feature values that were actually known as of
  that moment (no leaking future data into a training set).
- `store.materialize(...)` copies the latest known values from that exact
  same source into a low-latency **online store** (SQLite in this demo;
  Redis/DynamoDB in production — the online-store backend is swappable
  without touching the feature definition).
- `store.get_online_features(...)` — used for **serving** — does a
  key-value lookup against that online store.

Both paths trace back to the same `FeatureView`. There's no second
implementation of "how do I compute engagement_score" for the churn model
to accidentally diverge from — it's the same code path, just materialized
to two different places for two different access patterns (batch,
point-in-time for training; low-latency, current-value for serving).

## What's in this demo

```
feature_repo/
  feature_store.yaml        local provider, SQLite online store — no external infra
  features.py                the customer_id entity + customer_engagement_features FeatureView
  generate_data.py           (re-)generates the fake dataset below (fixed seed, deterministic)
  data/
    customer_features.csv    human-readable fake data: 20 customers x 2 daily snapshots
    customer_features.parquet   same data, in the columnar format Feast's FileSource reads
  demo.py                    the runnable end-to-end walkthrough (see below)
```

The fake data models exactly the kind of signals a production
feature-serving layer would compute — `engagement_score`, `days_since_last_contact`,
`total_conversations` — for 20 fake customers (`cust_001`..`cust_020`),
each with two snapshots a few days apart, so there's real history to
demonstrate point-in-time-correct retrieval against.

## Running the demo

```bash
cd feature_repo
python -m venv ../.venv && source ../.venv/bin/activate   # or your own venv
pip install -r ../requirements.txt
python demo.py
```

`demo.py` walks through the whole pipeline end to end, with clear
before/after output at each step:

1. **`feast apply`** — registers the entity + feature view from
   `features.py` into the local registry.
2. **Offline retrieval for training**, "as of" a timestamp deliberately
   *before* the second data snapshot — proving Feast returns the day-1
   values only, with no leakage of day-2 data into a training set frozen
   in the past.
3. **`materialize`** — copies the freshest known values into the online
   (SQLite) store.
4. **Online lookup for serving**, "right now" — returns each customer's
   *latest* materialized (day-2) values via a fast key-value read.
5. **Before vs. after** — the same feature, same customers, printed side
   by side at the two points in time, so the difference between "frozen
   training snapshot" and "live serving value" is visible directly.
6. **Train/serve consistency check** — re-runs the *offline* historical
   query but "as of now" instead of "as of training time," and asserts it
   matches what the online store is currently serving, customer by
   customer. This is the guarantee described above made concrete: the
   same `FeatureView` backs both paths, so there's no way for them to
   silently drift apart.

Sample output (abridged):

```
STEP 5: BEFORE (training, day-1 snapshot) vs AFTER (serving, day-2 snapshot)
customer_id  engagement_score_at_training_time  engagement_score_now_online  delta
   cust_002                              0.689                        0.885  0.196
   cust_006                              0.659                        0.487 -0.172
   ...

STEP 6: train/serve CONSISTENCY CHECK (offline 'as of now' vs online 'now')
MATCH — offline-as-of-now and online agree for all 20 customers.
```

## Notes on this being a *local* setup

- `provider: local` + `online_store: sqlite` in
  [`feature_store.yaml`](./feature_repo/feature_store.yaml) means the
  "online store" is a real online store (same `get_online_features()`
  interface a production Redis/DynamoDB backend would expose) — it's just
  backed by a local SQLite file instead of a server, so this demo needs
  zero external infrastructure.
- The CSV is the readable source of truth; Feast's local `FileSource`
  offline store needs a columnar format it can infer a schema from for
  point-in-time joins (Parquet/Avro/JSON — not CSV), so
  `generate_data.py` also writes an identical Parquet copy that
  `features.py` actually points at. Re-run `python generate_data.py` if
  you want to regenerate the fake dataset (it's deterministic given the
  fixed random seed).
- Swapping this for a production setup later is a `feature_store.yaml`
  change (e.g. `online_store: redis`, `offline_store: bigquery`/`snowflake`),
  not a rewrite of `features.py` or of any model's training/serving code —
  that's the actual point of the abstraction.
