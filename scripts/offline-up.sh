#!/usr/bin/env bash
# offline-up.sh — bring the whole stack up even when container-level
# internet egress is broken.
#
# WHY THIS EXISTS: on some networks, Docker Desktop's containers cannot make
# outbound TCP connections at all (pip installs / huggingface downloads /
# `ollama pull` all fail with connection timeouts), even though the HOST
# machine and `docker pull` (which uses a different, daemon-level path) both
# work fine. This is a real, reproduced issue -- see the root README's
# Quick Start section for the full diagnosis (every Docker Desktop /
# macOS-side fix was tried: VPN extension,
# reboot, TTL, MTU, hotspot power-cycle, version update, full VM wipe, VM
# backend switch -- none of it fixed the underlying network path). Rather
# than depend on that ever being fixed, this script routes around it: it
# downloads everything containers would normally fetch themselves (Python
# wheels, the embedding model, the Ollama model) on the HOST instead, using
# the host's own working internet connection, and hands the results to the
# containers via pre-built offline images / docker cp.
#
# USAGE:
#   ./scripts/offline-up.sh              # auto-detects: normal path if
#                                         # egress works, offline path if not
#   ./scripts/offline-up.sh --force-offline   # always use the offline path
#
# This script is idempotent -- safe to re-run. It cleans up its own
# temporary wheel/model caches when done; nothing it downloads is left
# behind in the repo.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root

FORCE_OFFLINE="${1:-}"
SCRATCH="$(mktemp -d -t aiops-offline-up)"
trap 'rm -rf "$SCRATCH"' EXIT

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------------------
# 1. Decide: normal path or offline path?
# ---------------------------------------------------------------------------

check_egress() {
  docker run --rm alpine:latest sh -c \
    "wget -q -O- --timeout=6 https://pypi.org >/dev/null 2>&1" \
    && return 0 || return 1
}

if [ "$FORCE_OFFLINE" != "--force-offline" ]; then
  log "Checking whether containers can reach the internet..."
  if check_egress; then
    log "Container egress works -- using the normal build path."
    docker compose up -d --build
    log "Done. Run 'docker compose ps' to check status."
    exit 0
  fi
  log "Container egress is broken (containers can't reach pypi.org, even though docker pull and the host both work)."
  log "Falling back to the offline path -- downloading dependencies on the host instead."
fi

# ---------------------------------------------------------------------------
# 2. Figure out the target platform (must match the containers' arch)
# ---------------------------------------------------------------------------

ARCH="$(docker version --format '{{.Server.Arch}}')"
case "$ARCH" in
  arm64|aarch64) PIP_PLATFORM="manylinux2014_aarch64" ;;
  amd64|x86_64)  PIP_PLATFORM="manylinux2014_x86_64" ;;
  *) echo "Unknown docker server arch '$ARCH' -- edit this script's platform mapping." >&2; exit 1 ;;
esac
log "Target platform: $PIP_PLATFORM (python 3.11, cp311)"

if ! command -v pip3 >/dev/null 2>&1; then
  echo "pip3 not found on the host -- needed to download wheels. Install Python 3 first." >&2
  exit 1
fi

PIP_DOWNLOAD=(pip3 download --platform "$PIP_PLATFORM" --python-version 311 --implementation cp --abi cp311 --only-binary=:all:)

# ---------------------------------------------------------------------------
# 3. Download wheels for each custom service, on the host
# ---------------------------------------------------------------------------

download_wheels() {
  local service="$1"; shift
  local dir="services/$service/_offline_wheels"
  rm -rf "$dir"; mkdir -p "$dir"
  log "Downloading $service's dependencies as wheels..."
  "${PIP_DOWNLOAD[@]}" -r "services/$service/requirements.txt" -d "$dir" "$@" >/dev/null
  # greenlet is a transitive sqlalchemy dependency that `pip download -r`
  # sometimes misses (observed directly -- see eval-service/mlflow training
  # build failures during this script's own development). Cheap to always
  # include; harmless if unused.
  "${PIP_DOWNLOAD[@]}" greenlet -d "$dir" >/dev/null 2>&1 || true
}

download_wheels llm-gateway
download_wheels agent-service
download_wheels eval-service
download_wheels rag-service
download_wheels feature-store
download_wheels mlflow
pip3 download torch --index-url https://download.pytorch.org/whl/cpu \
  --platform "$PIP_PLATFORM" --python-version 311 --implementation cp --abi cp311 \
  --only-binary=:all: -d "services/rag-service/_offline_wheels" >/dev/null
# feast (feature-store) pulls in dask, which needs importlib_metadata on
# Python <3.12 -- `pip download -r` sometimes misses this transitive dep too
# (same class of issue as greenlet above, observed directly).
pip3 download importlib_metadata \
  --platform "$PIP_PLATFORM" --python-version 311 --implementation cp --abi cp311 \
  --only-binary=:all: -d "services/feature-store/_offline_wheels" >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# 4. Pre-fetch the sentence-transformers embedding model rag-service needs
# ---------------------------------------------------------------------------

log "Pre-fetching the embedding model (all-MiniLM-L6-v2)..."
python3 -m venv "$SCRATCH/hf-venv"
"$SCRATCH/hf-venv/bin/pip" install -q huggingface_hub
"$SCRATCH/hf-venv/bin/python" -c "
from huggingface_hub import snapshot_download
snapshot_download('sentence-transformers/all-MiniLM-L6-v2', cache_dir='$SCRATCH/hf-cache')
"
rm -rf services/rag-service/_offline_hf_cache
mkdir -p services/rag-service/_offline_hf_cache
cp -r "$SCRATCH/hf-cache/"* services/rag-service/_offline_hf_cache/

# ---------------------------------------------------------------------------
# 5. Build the 6 custom images offline, tagged exactly as docker-compose.yml
#    expects, then clean up the wheel/model caches (not meant to live in
#    the repo -- they're multi-hundred-MB and fully reproducible)
# ---------------------------------------------------------------------------

log "Building llm-gateway (offline)..."
docker build -f services/llm-gateway/Dockerfile.offline -t llm-gateway:latest services/llm-gateway

log "Building agent-service (offline)..."
docker build -f services/agent-service/Dockerfile.offline -t agent-service:local services/agent-service

log "Building eval-service (offline)..."
docker build -f services/eval-service/Dockerfile.offline -t aiops-eval-service:local services/eval-service

log "Building rag-service (offline, this one takes a while -- torch + the embedding model)..."
docker build -f services/rag-service/Dockerfile.offline -t hands-on-project/rag-service:latest services/rag-service

log "Building feature-store (offline)..."
docker build -f services/feature-store/Dockerfile.offline -t feature-store:local services/feature-store

log "Building mlflow-training (offline) -- the one-shot job that auto-seeds a trained model..."
docker build -f services/mlflow/Dockerfile.offline -t mlflow-training:local services/mlflow

for svc in llm-gateway agent-service eval-service rag-service feature-store mlflow; do
  rm -rf "services/$svc/_offline_wheels"
done
rm -rf services/rag-service/_offline_hf_cache

# ---------------------------------------------------------------------------
# 6. Bring up the rest of the stack. Pre-built third-party images (postgres,
#    mlflow, langfuse-*, prometheus, grafana) and web-ui (nginx + static
#    files, no pip install) all build/pull fine even with broken egress --
#    only the 6 services above actually needed the workaround. Bringing the
#    full stack up here also starts `mlflow-training`, which auto-seeds a
#    trained model on first boot -- see services/mlflow/entrypoint.sh.
# ---------------------------------------------------------------------------

log "Building web-ui (plain nginx + static files, no pip deps -- doesn't need the offline path)..."
# NOTE: deliberately NOT using `docker compose up --build web-ui` here -- that
# triggers Compose's buildx-bake path, which (observed directly) rebuilds
# EVERY service in the compose file via its plain Dockerfile, not just
# web-ui, undoing all the offline-image building above and re-hitting the
# same broken-egress pip installs. A plain `docker build` targets only this
# one image.
docker build -t web-ui:local services/web-ui

log "Bringing up the full stack..."
docker compose up -d

# ---------------------------------------------------------------------------
# 7. Pull the Ollama model -- try the normal way first, fall back to
#    fetching the manifest/blobs directly on the host (Ollama's registry is
#    a plain OCI registry, so this is just HTTPS GETs) and injecting them
#    via `docker cp` if the container-level pull fails.
# ---------------------------------------------------------------------------

OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:0.5b}"
if docker compose exec -T ollama ollama list 2>/dev/null | grep -q "^${OLLAMA_MODEL%%:*}"; then
  log "Ollama model ($OLLAMA_MODEL) already present -- skipping pull."
elif docker compose exec -T ollama ollama pull "$OLLAMA_MODEL" 2>/dev/null; then
  log "Ollama model pulled normally."
else
  log "Container-level Ollama pull failed too -- fetching the model on the host instead."
  MODEL_NAME="${OLLAMA_MODEL%%:*}"
  MODEL_TAG="${OLLAMA_MODEL##*:}"
  MDIR="$SCRATCH/ollama-model"
  mkdir -p "$MDIR/manifests/registry.ollama.ai/library/$MODEL_NAME" "$MDIR/blobs"

  curl -sL "https://registry.ollama.ai/v2/library/$MODEL_NAME/manifests/$MODEL_TAG" \
    -o "$MDIR/manifests/registry.ollama.ai/library/$MODEL_NAME/$MODEL_TAG"

  digests=$(python3 -c "
import json
m = json.load(open('$MDIR/manifests/registry.ollama.ai/library/$MODEL_NAME/$MODEL_TAG'))
print(m['config']['digest'].split(':')[1])
for l in m['layers']:
    print(l['digest'].split(':')[1])
")
  for d in $digests; do
    curl -sL "https://registry.ollama.ai/v2/library/$MODEL_NAME/blobs/sha256:$d" -o "$MDIR/blobs/sha256-$d"
  done

  docker cp "$MDIR/manifests" "ollama:/root/.ollama/models/manifests"
  docker cp "$MDIR/blobs/." "ollama:/root/.ollama/models/blobs/"
  # docker cp nests one directory level deeper than expected when the
  # destination didn't already exist -- flatten it back (observed directly).
  docker exec ollama sh -c "
    if [ -d /root/.ollama/models/manifests/manifests ]; then
      mv /root/.ollama/models/manifests/manifests/* /root/.ollama/models/manifests/ 2>/dev/null
      rmdir /root/.ollama/models/manifests/manifests
    fi
  "
  docker restart ollama >/dev/null
  sleep 5
  log "Ollama model injected directly. Verifying:"
  docker exec ollama ollama list
fi

log "Stack is up. Next steps:"
echo "  - The urgency-classifier model trains and registers itself automatically on first"
echo "    boot (see the mlflow-training service / services/mlflow/entrypoint.sh) -- nothing"
echo "    to run by hand. To re-run the full train/promote/rollback narration on demand"
echo "    anyway, use services/mlflow/run_training.sh."
echo "  - Re-ingest any custom knowledge-base docs via POST /ingest (rag-service starts with only the seeded demo docs)"
echo "  - docker compose ps    # confirm everything is healthy (mlflow-training shows 'Exited (0)' once its one-shot job finishes -- that's success, not a crash)"
