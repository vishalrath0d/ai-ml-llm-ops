# MLflow — experiment tracking & model registry, actually wired into a live service

## Why this exists

It's common for a team with no experiment tracking and no model registry to
end up here: when someone trains or fine-tunes a model, there's no durable,
queryable record of which hyperparameters/data/code produced which model,
what its metrics were, which version is "the one currently in production,"
or how to get back to a known-good version if a new one turns out to be
worse. In practice, "rollback" ends up meaning someone remembering which
file/branch/S3 object was the old model — un-auditable, doesn't scale past
one person's memory.

MLflow gives you three things a setup like that lacks: **experiment
tracking** (every
run's params/metrics/artifacts recorded automatically), a **Model Registry**
(versioned independently of git/S3 layout), and **aliases** (`champion` /
`challenger` — a named pointer at a specific version; promotion = moving the
alias forward, rollback = moving it back, no redeploy, no code change).

**This is not just a registry-bookkeeping demo.** `agent-service` actually
loads whichever version is tagged `champion` for `support-urgency-classifier`
and uses it, live, on every `/chat` request (see
`../agent-service/app/model_registry.py`). Flipping the alias here changes
`agent-service`'s real behavior — this README's walkthrough proves that with
an actual before/after example, not just registry UI screenshots.

## Files

- `compose.fragment.yml` — the MLflow tracking server (port 5000 internally,
  5050 on the host), backed by Postgres, with artifacts proxied through the
  server's own REST API (see "gotchas" below for why that specific detail
  matters).
- `train_and_log.py` — trains a real, usable model: message TEXT (TF-IDF)
  combined with LIVE customer-context features from Feast's online store
  (`engagement_score`, `days_since_last_contact`, `total_conversations`,
  `open_tickets` — see `../feature-store/`), via a `ColumnTransformer` +
  Logistic Regression, to label a support message "urgent" or "normal" —
  on a small hand-labeled set of Onwly-support-style messages (the same
  fictional product `rag-service`'s knowledge base uses), including
  deliberately ambiguous text a text-only model can't classify better than
  chance on, so the training set actually forces the model to use both
  signals (14 clear-cut examples per class + 4 borderline ones trained
  twice each, against a 12-row held-out test set -- deliberately sized up
  from an earlier 8-row test set specifically to make the accuracy number
  less noisy; one misclassification now swings it by ~8 points instead of
  ~12.5). Registers it, promotes a deliberately-worse candidate, detects
  the regression, rolls back — exactly like a real production ML pipeline
  would want to, just concrete. See `../feature-store/README.md`'s "Live integration"
  section for the concrete before/after (same message, different customer,
  different urgency label).

  Logs **precision/recall/F1 per class**, not just accuracy — for an
  urgency classifier specifically, missing a genuinely urgent message
  (low `recall_urgent`) is a costlier mistake than the reverse, and a
  single "accuracy" number can hide a model that's specifically bad at
  the error that matters. Open a run in the MLflow UI's Metrics tab to see
  `accuracy`, `precision_urgent`, `recall_urgent`, `f1_urgent` (and the
  `_normal` equivalents) side by side.
- `run_training.sh` — runs `train_and_log.py` **manually, on demand**, to
  watch the full train/promote/rollback narration happen (see "the
  Python-version gotcha" below for why this exists instead of "just pip
  install and run it").
- `Dockerfile` / `Dockerfile.offline` / `entrypoint.sh` — power the
  `mlflow-training` service in the root `docker-compose.yml`, which runs
  this **automatically** on `docker compose up --build`, on ANY machine
  (yours or a teammate's), so a fresh clone is never left with an untrained
  registry. See "Running it" below for exactly how this and
  `run_training.sh` relate (they solve different problems, not the same
  one twice).
- `requirements.txt` — Python deps, shared by the manual script and the
  automatic container.

## Three real gotchas found and fixed during verification (worth knowing regardless of this project)

1. **`--default-artifact-root` vs `--artifacts-destination`.** The compose
   fragment originally used `--default-artifact-root /mlartifacts`, which
   hands remote clients a raw filesystem path and expects them to have that
   exact path mounted — which a script running on your host machine never
   does. This failed with a real `OSError: [Errno 30] Read-only file
   system: '/mlartifacts'`. Fixed by switching to `--artifacts-destination`,
   which keeps MLflow's default proxied-artifact mode intact (the server
   streams uploads/downloads through its own REST API — the client needs
   network access only, not filesystem access). **If you ever see that
   exact OSError against a real MLflow server, this is almost certainly why.**
2. **`--allowed-hosts`.** MLflow 3.x validates the request's `Host` header to
   prevent DNS-rebinding attacks, and by default only allows `localhost` +
   raw private-IP literals — NOT docker-compose service hostnames like
   `mlflow`. `agent-service`'s requests were rejected with a real `403
   "Invalid Host header - possible DNS rebinding attack detected"` until
   `--allowed-hosts "*"` was added. That wildcard is explicitly documented
   by MLflow as "not recommended for production" — correct as the default
   here because this stack starts out never leaving your machine, but
   that stops being true in a real staging/prod environment (see
   `../../docs/operations/environments.md`) — it's now env-var-driven (`MLFLOW_ALLOWED_HOSTS`
   in `docker-compose.yml`), not a permanent hardcoded constant.
3. **Unpinned `scikit-learn`/`pandas` ranges across the train/serve
   boundary.** `requirements.txt` here and `../agent-service/requirements.txt`
   both used to say `scikit-learn>=1.3,<2` — which let training (whenever
   this script last ran) and serving (whenever agent-service's image was
   last built) independently resolve *different* versions within that
   range. Confirmed live: agent-service logged a real
   `InconsistentVersionWarning: Trying to unpickle estimator ... from
   version 1.7.0 when using version 1.9.0`. Fixed by exact-pinning both
   files to the identical `scikit-learn==1.7.0` / `pandas==2.3.2` /
   `mlflow==3.15.1` (`mlflow-skinny` on agent-service's side) — and by
   pinning `train_and_log.py`'s own `pip_requirements=[...]` passed to
   `mlflow.pyfunc.log_model(...)` to the exact installed versions
   (`sklearn.__version__` etc.) rather than a bare unpinned package-name
   list, so the *logged model artifact* also states precisely what it
   needs. **If you ever see that exact warning against a real MLflow
   setup, this train/serve version-skew is almost certainly why** — and
   the versions chosen here specifically had to be double-checked against
   `scripts/offline-up.sh`'s fallback path too: `pip download --platform
   manylinux2014_aarch64` only has wheels for `scikit-learn` up through
   `1.7.0` under that tag, so pinning to a newer 1.x (which installs fine
   via a *normal* build) would have silently broken the offline path.

## The Python-version gotcha — why `run_training.sh` exists, not just `pip install -r requirements.txt && python train_and_log.py`

A model trained under a different Python minor version than the one loading
it can crash the *loading* process outright, not just warn. This happened
for real during verification: `train_and_log.py` run from a Python 3.13 host
venv produced a model that, when `agent-service` (Python 3.11, per its
Dockerfile) tried to load and predict with it, crashed the whole worker
process with no clean Python traceback — `mlflow.pyfunc`'s custom
`PythonModel` wrapping uses `cloudpickle`, which is not reliably compatible
across Python minor versions for objects embedding compiled numpy/scikit-learn
state.

`run_training.sh` sidesteps this by running the training script inside a
`python:3.11-slim` container — the exact same base image `agent-service`
itself runs on — regardless of whatever Python version happens to be on your
host. **Always use `run_training.sh`, not a host venv,** unless you've
specifically confirmed your host Python matches 3.11.

## Running it

**You don't have to run anything to get a trained model** — `docker compose
up --build` (from the repo root) already includes a `mlflow-training`
service that runs this exact training/promotion/rollback exercise
automatically, once, the first time the stack ever comes up on a given
machine (it detects whether `support-urgency-classifier` is already
registered and skips training if so — see `entrypoint.sh`). `docker compose
ps` will show `mlflow-training` as `Exited (0)` once it's done; that's
success. This is what makes a fresh `git clone` + `docker compose up --build`
on a teammate's machine come up already-trained, with zero manual steps.

To **re-run the narration yourself and watch it happen** (or force a fresh
train/promote/rollback cycle even though a model already exists):

1. Bring up the stack (`docker compose up -d` from the repo root, or at
   minimum `mlflow`, `postgres`, and `agent-service`).
2. Run the training/promotion/rollback script:
   ```bash
   cd services/mlflow
   ./run_training.sh
   ```
3. Watch the printed narration (real output from a verification run):
   ```
   STEP 1: training baseline model (run 1) on the full training set
     logged run 'baseline-v1' (run_id=...) accuracy=0.9167 recall_urgent=0.8333 on 24 examples
   BASELINE: v3 registered and tagged champion (accuracy=0.9167)

   STEP 2: training a new candidate (run 2) on a deliberately skewed slice of data
     logged run 'candidate-v2-skewed-data' (run_id=...) accuracy=0.5000 recall_urgent=0.0000 on 10 examples
   NEW CANDIDATE: v4 registered (accuracy=0.5000)

   STEP 3: promoting the new candidate to champion
   PROMOTING v4 to champion

   STEP 4: comparing candidate vs baseline accuracy to decide whether to keep it
     v3 (previous champion) accuracy = 0.9167
     v4 (current champion)  accuracy = 0.5000
   REGRESSION DETECTED, ROLLING BACK: champion -> v3
   ```
   (Version numbers keep climbing each time you re-run this — v3/v4 here,
   not v1/v2, simply because earlier runs on this same machine already
   registered v1/v2. `recall_urgent=0.0000` on the skewed candidate is the
   sharpest signal of the regression: it didn't just get *worse* at
   detecting urgent messages, it stopped detecting them entirely.)
   The candidate is deliberately trained on a skewed slice of the data (a
   realistic way a real regression happens — someone re-trains on a bad
   data pull) so its held-out accuracy is worse than the baseline's. The
   script compares the two logged accuracies itself and rolls back
   automatically.
4. **Now prove it's not just registry bookkeeping** — force `agent-service`
   to pick up the current champion immediately (it also self-refreshes every
   60s on its own) and watch the SAME message get classified differently
   depending on which version is champion:
   ```bash
   curl -X POST localhost:8003/admin/reload-model
   curl -X POST localhost:8003/chat -H 'Content-Type: application/json' \
     -d '{"session_id":"demo","message":"This is the third time your product has broken and I am furious, fix it right now!"}'
   # -> "urgency":"urgent" under the good v1/v3 model
   ```
   Then flip `champion` to the bad version directly and repeat the exact
   same message:
   ```bash
   python3 -c "
   from mlflow import MlflowClient
   c = MlflowClient(tracking_uri='http://localhost:5050')
   c.set_registered_model_alias('support-urgency-classifier', 'champion', 2)  # the bad one
   "
   curl -X POST localhost:8003/admin/reload-model
   curl -X POST localhost:8003/chat -H 'Content-Type: application/json' \
     -d '{"session_id":"demo2","message":"This is the third time your product has broken and I am furious, fix it right now!"}'
   # -> "urgency":"normal" -- same message, worse model, wrong answer.
   # Confirmed directly during verification: this exact flip changed the
   # classification and measurably softened the response's tone (no
   # empathy/escalation language) with zero code change or redeploy.
   ```
   Roll it back the same way (`set_registered_model_alias(..., 1)`) to
   restore correct behavior — that's the entire "how do I revert" answer,
   as a lived example instead of a hypothetical.
5. Open http://localhost:5050 → **Models** → `support-urgency-classifier` →
   **Aliases** to see the same story in the registry UI.

## What to try next

- Check `GET localhost:8003/admin/model-status` any time to see which
  version is currently loaded without forcing a reload.
- Watch `agent_service_urgency_model_version_loaded` and
  `agent_service_urgency_classifications_total` in Prometheus/Grafana —
  the version-loaded gauge is the concrete "did my promotion actually reach
  production" signal an AIOps dashboard would give you for this exact
  scenario.
- Re-train v2 on the FULL (non-skewed) dataset with different
  hyperparameters and confirm a real improvement gets promoted and stays —
  i.e., the "no regression, keeping the new version" branch of the script.
