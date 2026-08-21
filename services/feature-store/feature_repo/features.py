"""Feast feature definitions for the churn-prediction demo.

This models what a production feature-serving layer already
computes per-conversation (engagement_score, days_since_last_contact,
total_conversations) but currently has no way to share with any other
model or service. See ../README.md for the full narrative.

`feast apply` (run from demo.py or `feast apply` on the CLI, from this
directory) reads this module and registers the entity + feature view into
the local registry (data/registry.db).
"""
from datetime import timedelta

from feast import Entity, FeatureView, Field, FileSource
from feast.types import Float32, Int64
from feast.value_type import ValueType

# --- Entity ---------------------------------------------------------------
# The join key every feature in this repo is keyed on. In a real system
# this would be the same customer_id used across the feature-serving layer,
# billing, and any future churn model.
customer = Entity(
    name="customer_id",
    join_keys=["customer_id"],
    value_type=ValueType.STRING,
    description="Unique identifier for a customer/conversation owner.",
)

# --- Source -----------------------------------------------------------------
# Stands in for what would, in production, be a table in the warehouse that
# a production feature-serving layer already writes to. Feast reads this for
# *offline* (historical/training) retrieval; `materialize` copies the
# freshest row per entity into the online store for low-latency serving.
#
# The human-readable source of truth is data/customer_features.csv (open it
# directly to see the fake data). Feast's local FileSource offline store
# needs a format it can infer a schema from for point-in-time joins, which
# rules out plain CSV — so generate_data.py also writes an identical
# Parquet copy, and that's what's referenced here.
customer_engagement_source = FileSource(
    name="customer_engagement_source",
    path="data/customer_features.parquet",
    timestamp_field="event_timestamp",
)

# --- Feature View -----------------------------------------------------------
# One definition, two consumers: get_historical_features() (offline/training)
# and get_online_features() (online/serving) both read through this same
# FeatureView, which is the whole point — see the README's "train/serve
# skew" section.
customer_engagement_features = FeatureView(
    name="customer_engagement_features",
    entities=[customer],
    ttl=timedelta(days=3650),  # generous TTL for a demo; production would tune this
    schema=[
        Field(name="engagement_score", dtype=Float32),
        Field(name="days_since_last_contact", dtype=Int64),
        Field(name="total_conversations", dtype=Int64),
        # Added when this feature view went from a standalone teaching demo
        # to a real input to agent-service's live urgency classifier (see
        # ../../mlflow/train_and_log.py and
        # ../../agent-service/app/feature_store_client.py) -- this is the
        # concrete signal that lets "urgent-sounding text from a customer
        # with several open tickets" score higher than the same text alone.
        Field(name="open_tickets", dtype=Int64),
    ],
    online=True,
    source=customer_engagement_source,
    tags={"owner": "feature-store-demo", "team": "ai-ml-llm-ops"},
)
