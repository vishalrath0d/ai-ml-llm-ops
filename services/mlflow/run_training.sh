#!/usr/bin/env bash
# run_training.sh — runs train_and_log.py in a container matching
# agent-service's EXACT Python version (3.11-slim), on the same docker
# network as the rest of the stack.
#
# WHY THIS EXISTS, NOT "just pip install and run it from your own venv":
# a real, reproduced crash during this project's own verification. A model
# trained under Python 3.13 (a host venv) and then loaded by agent-service
# (Python 3.11, per its Dockerfile) caused agent-service's worker process
# to crash outright on the first prediction -- cloudpickle-serialized
# custom PythonModel objects embedding numpy/scikit-learn state are not
# reliably compatible across Python minor versions. Training inside the
# same Python version agent-service actually runs eliminates this whole
# class of bug, and doesn't depend on whatever Python version happens to
# be on your host machine.
#
# If container-level internet egress is broken on your network (containers
# can't pip install even though the host and `docker pull` both work -- see
# the root README's Quick Start section and ../../scripts/offline-up.sh),
# this script auto-detects that and downloads the wheels on the host
# instead, the same way offline-up.sh does for the other services.
set -euo pipefail
cd "$(dirname "$0")"

NETWORK="ai-ml-llm-ops_default"
if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "Error: docker network '$NETWORK' not found -- is the stack up? (docker compose up -d)" >&2
  exit 1
fi

if docker run --rm --network "$NETWORK" python:3.11-slim \
     sh -c "wget -q -O- --timeout=6 https://pypi.org >/dev/null 2>&1"; then
  docker run --rm \
    --network "$NETWORK" \
    -v "$(pwd):/app" \
    -w /app \
    -e MLFLOW_TRACKING_URI=http://mlflow:5000 \
    python:3.11-slim \
    bash -c "pip install -q -r requirements.txt && python train_and_log.py"
else
  echo "Container egress is broken -- downloading dependencies on the host instead." >&2
  ARCH="$(docker version --format '{{.Server.Arch}}')"
  case "$ARCH" in
    arm64|aarch64) PLATFORM="manylinux2014_aarch64" ;;
    amd64|x86_64)  PLATFORM="manylinux2014_x86_64" ;;
    *) echo "Unknown docker server arch '$ARCH'" >&2; exit 1 ;;
  esac
  SCRATCH="$(mktemp -d)"
  trap 'rm -rf "$SCRATCH"' EXIT
  pip3 download -r requirements.txt \
    --platform "$PLATFORM" --python-version 311 --implementation cp --abi cp311 \
    --only-binary=:all: -d "$SCRATCH/wheels" >/dev/null
  # greenlet is a transitive sqlalchemy dependency `pip download -r` sometimes
  # misses -- see offline-up.sh's comment on the same issue.
  pip3 download greenlet --platform "$PLATFORM" --python-version 311 \
    --implementation cp --abi cp311 --only-binary=:all: -d "$SCRATCH/wheels" >/dev/null 2>&1 || true

  docker run --rm \
    --network "$NETWORK" \
    -v "$(pwd):/app" \
    -v "$SCRATCH/wheels:/wheels" \
    -w /app \
    -e MLFLOW_TRACKING_URI=http://mlflow:5000 \
    python:3.11-slim \
    bash -c "pip install -q --no-index --find-links=/wheels -r requirements.txt && python train_and_log.py"
fi
