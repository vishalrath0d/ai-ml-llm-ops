# feature-store — serves Feast's online feature store over HTTP
# (`feast serve`), the same "every capability is its own HTTP service"
# pattern this project uses for rag-service/llm-gateway, rather than having
# agent-service import feast in-process.
#
# The registry (data/registry.db) and online store (data/online_store.db)
# are built at IMAGE BUILD TIME, not container startup: this is Feast's
# local/file-based provider serving a fixed, deterministic fake dataset
# (see feature_repo/generate_data.py) that never changes at runtime, so
# there's no reason to redo `feast apply`/`materialize` on every container
# start. A real deployment (Redis/DynamoDB online store, a warehouse
# offline store, data that actually changes) would materialize on a
# schedule instead -- see feature_repo/README.md's demo.py for the
# "materialize as of now" pattern this build step is a static version of.
FROM python:3.11-slim

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /usr/sbin/nologin --create-home appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY feature_repo ./feature_repo
WORKDIR /app/feature_repo

# `feast apply`/`materialize` must run as the SAME user that will later run
# `feast serve` -- the registry/online-store db files it writes are owned by
# whoever ran this step, and `feast serve` needs write access to them (the
# local file-based online store isn't strictly read-only at serve time).
RUN chown -R appuser:appuser /app
USER appuser

RUN feast apply \
    && feast materialize-incremental 2030-01-01T00:00:00

EXPOSE 6566

CMD ["feast", "serve", "--host", "0.0.0.0", "--port", "6566"]
