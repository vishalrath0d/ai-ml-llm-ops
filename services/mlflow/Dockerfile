# mlflow-training — the one-shot job that seeds a real, trained
# `support-urgency-classifier` model into MLflow's registry on first boot.
# See entrypoint.sh for why this exists and why it's safe to run on every
# `docker compose up` (idempotent -- skips training once a model already
# exists in the persisted volume).
#
# python:3.11-slim, not whatever your host happens to run -- same reasoning
# as run_training.sh: a model trained under a different Python minor
# version than the one LOADING it (agent-service, also 3.11-slim) can crash
# the loading process outright. See README.md's "Python-version gotcha".
FROM python:3.11-slim

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /usr/sbin/nologin --create-home appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY train_and_log.py entrypoint.sh ./
RUN chmod +x entrypoint.sh && chown -R appuser:appuser /app

USER appuser

CMD ["./entrypoint.sh"]
