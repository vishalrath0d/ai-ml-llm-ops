#!/usr/bin/env bash
# entrypoint.sh — runs automatically as part of `docker compose up --build`
# (see the `mlflow-training` service in the root docker-compose.yml), NOT
# something a teammate has to remember to run by hand.
#
# WHY THIS EXISTS: without it, a fresh `git clone` + `docker compose up
# --build` on a teammate's machine gives you an MLflow registry with NO
# model registered at all -- agent-service degrades to "unknown" urgency
# forever, because nothing ever trained/promoted a `support-urgency-
# classifier` for it to load. This makes the very first startup on ANY
# machine (yours or a teammate's) self-sufficient: no manual step, no
# separate script to remember.
#
# Idempotent by design: it only actually trains once, on the first-ever
# startup (an empty `postgres_data`/`mlflow_artifacts` volume, i.e. no
# `support-urgency-classifier` registered yet). Every later `docker compose
# up` skips training entirely -- the model, once trained, persists in
# those two named Docker volumes across restarts exactly like any other
# service's data (`docker compose down` keeps it; `docker compose down -v`
# deletes it, same as everything else in this project). Re-running the full
# train/promote/rollback narration on demand is still `run_training.sh`'s
# job, unchanged -- this script and that one solve different problems
# (auto-seed on first boot vs. manual "watch it happen" demo).
set -euo pipefail

MLFLOW_TRACKING_URI="${MLFLOW_TRACKING_URI:-http://mlflow:5000}"
MODEL_NAME="support-urgency-classifier"

echo "Waiting for mlflow to actually be reachable at ${MLFLOW_TRACKING_URI}..."
ready=""
for _ in $(seq 1 60); do
  if curl -sf "${MLFLOW_TRACKING_URI}/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [ -z "$ready" ]; then
  echo "mlflow never became reachable after 120s -- giving up. agent-service" >&2
  echo "will run with urgency classification degraded to 'unknown' until" >&2
  echo "you run services/mlflow/run_training.sh manually once mlflow is up." >&2
  exit 1
fi

status="$(curl -s -o /dev/null -w '%{http_code}' "${MLFLOW_TRACKING_URI}/api/2.0/mlflow/registered-models/get?name=${MODEL_NAME}")"
if [ "$status" = "200" ]; then
  echo "Model '${MODEL_NAME}' is already registered (found in the persisted mlflow volume) -- skipping training."
  echo "This is expected on every startup after the very first one. To force a fresh"
  echo "training/promotion/rollback run anyway, use services/mlflow/run_training.sh."
  exit 0
fi

echo "No registered model found -- this looks like a fresh startup. Running the initial"
echo "training/promotion/rollback exercise so agent-service has a real champion model..."
python train_and_log.py
