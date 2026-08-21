# --- builder stage: install dependencies into a venv ---
FROM python:3.11-slim AS builder

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
# Install CPU-only torch FIRST, from PyTorch's own CPU wheel index, before
# sentence-transformers pulls it in transitively. Without this, pip resolves
# a default torch build that drags in several GB of NVIDIA CUDA packages
# (nvidia-cusparselt-cu13, cuda-toolkit, etc.) that can never be used here --
# this is a CPU-only container on Apple Silicon (linux/arm64), no GPU is
# ever present. Observed directly during verification: this bloat was the
# single biggest cause of a rebuild taking 30+ minutes.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model at build time so first-request (and first
# startup seed) latency doesn't include a model download.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# --- runtime stage ---
FROM python:3.11-slim AS runtime

RUN groupadd --system app && useradd --system --gid app --home-dir /app --create-home app

COPY --from=builder /opt/venv /opt/venv
# Cached sentence-transformers/HF model files live in the builder's home dir.
COPY --from=builder /root/.cache /home/app/.cache

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CHROMA_PERSIST_DIR=/data/chroma \
    HF_HOME=/home/app/.cache/huggingface

WORKDIR /app

COPY app ./app
COPY seed.py keyword_triggers.yaml ./

RUN mkdir -p /data/chroma \
    && chown -R app:app /app /data /home/app/.cache

USER app

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
