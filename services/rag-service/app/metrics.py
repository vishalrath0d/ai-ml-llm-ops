"""Prometheus metrics for rag-service."""
from prometheus_client import Counter, Histogram

INGEST_COUNT = Counter(
    "rag_ingest_total",
    "Number of documents successfully ingested",
)

QUERY_COUNT = Counter(
    "rag_query_total",
    "Number of queries served",
    labelnames=("mode", "retrieval_path"),
)

QUERY_LATENCY_SECONDS = Histogram(
    "rag_query_latency_seconds",
    "Query latency in seconds, from request in to response out",
    labelnames=("mode",),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
