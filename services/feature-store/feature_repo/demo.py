#!/usr/bin/env python3
"""End-to-end Feast demo: feast apply -> offline (training) retrieval ->
materialize -> online (serving) retrieval -> train/serve consistency check.

Run from this directory (feature_repo/):

    python demo.py

Everything is local/file-based (see feature_store.yaml) — no external
services required.
"""
import os
import subprocess
import sys
from datetime import datetime

import pandas as pd
from feast import FeatureStore

FEATURE_REFS = [
    "customer_engagement_features:engagement_score",
    "customer_engagement_features:days_since_last_contact",
    "customer_engagement_features:total_conversations",
]

CUSTOMER_IDS = [f"cust_{i:03d}" for i in range(1, 21)]

# The two snapshot dates baked into data/customer_features.csv by
# generate_data.py — see that file for how the fake history was built.
DAY1 = datetime(2024, 6, 1)
DAY2 = datetime(2024, 6, 3)
TRAINING_AS_OF = datetime(2024, 6, 2)  # deliberately *between* day1 and day2
NOW = datetime(2024, 6, 5)  # "today" for materialize + the consistency check


def header(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> None:
    # ------------------------------------------------------------------
    # STEP 1 — feast apply: register the entity + feature view defined in
    # features.py into the local registry (data/registry.db).
    # ------------------------------------------------------------------
    header("STEP 1: feast apply")
    # Resolve the `feast` console script relative to the running
    # interpreter (same venv/bin dir as sys.executable) rather than
    # relying on it being on PATH — more robust across shells/containers.
    feast_bin = os.path.join(os.path.dirname(sys.executable), "feast")
    if not os.path.exists(feast_bin):
        feast_bin = "feast"  # fall back to PATH lookup
    result = subprocess.run([feast_bin, "apply"], capture_output=True, text=True)
    print(result.stdout.strip() or "(no stdout)")
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    store = FeatureStore(repo_path=".")

    # ------------------------------------------------------------------
    # STEP 2 — OFFLINE historical retrieval, for TRAINING.
    #
    # This is what a training pipeline does: for every customer, "as of"
    # some point in time (here: 2024-06-02, deliberately *before* the
    # second CSV snapshot on 2024-06-03), get the feature values that were
    # actually known at that moment. This is point-in-time correct — Feast
    # will NOT leak the 2024-06-03 values into a training set whose labels
    # were generated on 2024-06-02.
    # ------------------------------------------------------------------
    header(f"STEP 2: OFFLINE retrieval for TRAINING (as of {TRAINING_AS_OF.date()})")
    entity_df = pd.DataFrame(
        {
            "customer_id": CUSTOMER_IDS,
            "event_timestamp": [TRAINING_AS_OF] * len(CUSTOMER_IDS),
        }
    )
    training_df = (
        store.get_historical_features(entity_df=entity_df, features=FEATURE_REFS)
        .to_df()
        .sort_values("customer_id")
        .reset_index(drop=True)
    )
    print(f"(point-in-time correct — this is the day-1 snapshot, since day-2 is in the future relative to {TRAINING_AS_OF.date()})")
    print(training_df.to_string(index=False))

    # ------------------------------------------------------------------
    # STEP 3 — materialize the freshest known feature values into the
    # ONLINE store (sqlite here; Redis/DynamoDB in production). This is
    # the step that makes low-latency `get_online_features` lookups
    # possible at serving time.
    # ------------------------------------------------------------------
    header(f"STEP 3: materialize -> online store (through {NOW.date()})")
    store.materialize(start_date=DAY1, end_date=NOW)
    print(f"Materialized features from {DAY1.date()} through {NOW.date()} into the online store.")

    # ------------------------------------------------------------------
    # STEP 4 — ONLINE feature lookup, for SERVING (right now).
    #
    # A real-time churn-prediction model calls this at request time. It's a
    # key-value lookup (fast), and it returns each customer's *latest*
    # materialized values — the day-2 snapshot, since that's the most
    # recent data materialized.
    # ------------------------------------------------------------------
    header("STEP 4: ONLINE lookup for SERVING (right now)")
    online_response = store.get_online_features(
        features=FEATURE_REFS,
        entity_rows=[{"customer_id": cid} for cid in CUSTOMER_IDS],
    )
    online_df = online_response.to_df().sort_values("customer_id").reset_index(drop=True)
    print(online_df.to_string(index=False))

    # ------------------------------------------------------------------
    # STEP 5 — BEFORE vs AFTER: same feature, same customers, two points
    # in time. The values differ, and they *should* — the training set was
    # frozen as of 2024-06-02, serving happens "now" (2024-06-05) after a
    # newer snapshot landed. This is normal feature freshness, not skew.
    # ------------------------------------------------------------------
    header("STEP 5: BEFORE (training, day-1 snapshot) vs AFTER (serving, day-2 snapshot)")
    comparison = training_df[["customer_id", "engagement_score"]].merge(
        online_df[["customer_id", "engagement_score"]],
        on="customer_id",
        suffixes=("_at_training_time", "_now_online"),
    )
    comparison["delta"] = (
        comparison["engagement_score_now_online"] - comparison["engagement_score_at_training_time"]
    ).round(3)
    print(comparison.to_string(index=False))

    # ------------------------------------------------------------------
    # STEP 6 — TRAIN/SERVE CONSISTENCY CHECK.
    #
    # Re-run the *offline* historical query, but "as of now" instead of
    # "as of training time". If Feast's offline and online paths are both
    # backed by the same FeatureView (same source, same schema, same
    # transformation), querying "as of now" offline must return exactly
    # the values the online store is currently serving. This is the
    # guarantee a feature store gives you that hand-rolled training and
    # serving pipelines don't: one definition, so the numbers can't drift
    # apart from a copy-paste or reimplementation bug.
    # ------------------------------------------------------------------
    header("STEP 6: train/serve CONSISTENCY CHECK (offline 'as of now' vs online 'now')")
    entity_df_now = pd.DataFrame(
        {
            "customer_id": CUSTOMER_IDS,
            "event_timestamp": [NOW] * len(CUSTOMER_IDS),
        }
    )
    offline_now_df = (
        store.get_historical_features(entity_df=entity_df_now, features=FEATURE_REFS)
        .to_df()
        .sort_values("customer_id")
        .reset_index(drop=True)
    )

    merged = offline_now_df.merge(
        online_df, on="customer_id", suffixes=("_offline_as_of_now", "_online")
    )
    # engagement_score is declared Float32 in the FeatureView schema (see
    # features.py); the online store round-trips it through actual 32-bit
    # floats, so a value like 0.885 comes back as 0.8849999904632568 there
    # while the offline path (reading the Parquet file directly, still
    # float64 in memory) keeps the exact value. That's float32 precision
    # loss, not a train/serve skew — so the equality check below uses a
    # numeric tolerance for the float column and exact equality for the
    # integer columns.
    engagement_close = (
        (merged["engagement_score_offline_as_of_now"] - merged["engagement_score_online"])
        .abs()
        .lt(1e-4)
    )
    ints_equal = (
        merged["days_since_last_contact_offline_as_of_now"] == merged["days_since_last_contact_online"]
    ) & (merged["total_conversations_offline_as_of_now"] == merged["total_conversations_online"])
    mismatches = merged[~(engagement_close & ints_equal)]

    if mismatches.empty:
        print(
            f"MATCH — offline-as-of-now and online agree for all {len(merged)} customers.\n"
            "This is train/serve consistency: the same FeatureView definition served both\n"
            "paths, so there is no way for a training pipeline and a serving pipeline to\n"
            "silently compute 'engagement_score' two different ways."
        )
    else:
        print(f"MISMATCH found for {len(mismatches)} customer(s):")
        print(mismatches.to_string(index=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
