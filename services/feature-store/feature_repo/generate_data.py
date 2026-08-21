#!/usr/bin/env python3
"""Generates data/customer_features.csv — fake historical feature values for
20 customers, each with two snapshots (event_timestamp) a few days apart, so
demo.py has real point-in-time history to query.

The CSV is the human-readable source of truth (open it in any editor/
spreadsheet to see exactly what's in the demo). Feast's local FileSource
offline store only reads columnar formats it can infer a schema from
(Parquet/Avro/Json/Delta — not CSV), so this script also writes a Parquet
copy (data/customer_features.parquet) that features.py's FileSource
actually points at. Both files always describe the same rows.

This is a one-time data-generation script, not part of the demo pipeline
itself — both files it produces are checked into the repo. Re-run only if
you want to regenerate the fake dataset (deterministic given the fixed
seed):

    python generate_data.py

IMPORTANT: `feast apply` scans and imports every .py file in this directory
to discover feature definitions, so this file's work MUST stay inside
`main()` behind the `if __name__ == "__main__"` guard below — otherwise
every `feast apply` run would silently regenerate (and, without a fixed
seed, change) the dataset as an import side effect.
"""
import csv
import random
from datetime import datetime

import pandas as pd

OUT_PATH = "data/customer_features.csv"
PARQUET_PATH = "data/customer_features.parquet"
NUM_CUSTOMERS = 20
DAY1 = datetime(2024, 6, 1)
DAY2 = datetime(2024, 6, 3)

# cust_001/002/003 are the same three customers agent-service's mocked CRM
# (services/agent-service/app/tools_impl.py) already knows about -- hand-set
# to match their CRM personas instead of random, so the two data sources
# tell one coherent story instead of two disconnected fake datasets:
#   cust_001 Jane Doe   (Pro, active, 1 open ticket)       -> healthy-ish
#   cust_002 Amit Shah  (Enterprise, active, 0 tickets)    -> the "good" customer
#   cust_003 Maria Gomez (Free, past_due, 3 open tickets)  -> the "at-risk" customer
HAND_SET_PROFILES = {
    "cust_001": {"engagement_score": 0.72, "days_since_last_contact": 3, "total_conversations": 14, "open_tickets": 1},
    "cust_002": {"engagement_score": 0.93, "days_since_last_contact": 1, "total_conversations": 31, "open_tickets": 0},
    "cust_003": {"engagement_score": 0.18, "days_since_last_contact": 22, "total_conversations": 6, "open_tickets": 3},
}


def main() -> None:
    random.seed(42)
    rows = []
    for i in range(1, NUM_CUSTOMERS + 1):
        customer_id = f"cust_{i:03d}"

        if customer_id in HAND_SET_PROFILES:
            profile = HAND_SET_PROFILES[customer_id]
            rows.append({"customer_id": customer_id, "event_timestamp": DAY1.isoformat(), **profile})
            rows.append({"customer_id": customer_id, "event_timestamp": DAY2.isoformat(), **profile})
            continue

        # Day-1 snapshot: baseline engagement, as computed by
        # a production feature-serving layer a few days ago.
        day1_engagement = round(random.uniform(0.1, 0.9), 3)
        day1_days_since_contact = random.randint(0, 30)
        day1_total_conversations = random.randint(1, 50)
        # Open tickets tend to be higher for less-engaged customers (a
        # disengaged customer's issues pile up unresolved) -- weighted
        # random draw, not a strict function, so it's realistic rather than
        # a giveaway correlation.
        day1_open_tickets = min(5, int(random.expovariate(1.0 + day1_engagement * 3)))

        rows.append(
            {
                "customer_id": customer_id,
                "event_timestamp": DAY1.isoformat(),
                "engagement_score": day1_engagement,
                "days_since_last_contact": day1_days_since_contact,
                "total_conversations": day1_total_conversations,
                "open_tickets": day1_open_tickets,
            }
        )

        # Day-2 snapshot: engagement drifts (up or down),
        # days_since_last_contact resets lower if they had a new
        # conversation, total_conversations grows.
        drift = random.uniform(-0.25, 0.25)
        day2_engagement = round(min(1.0, max(0.0, day1_engagement + drift)), 3)
        day2_days_since_contact = max(0, random.randint(0, 5))
        day2_total_conversations = day1_total_conversations + random.randint(0, 4)
        day2_open_tickets = min(5, int(random.expovariate(1.0 + day2_engagement * 3)))

        rows.append(
            {
                "customer_id": customer_id,
                "event_timestamp": DAY2.isoformat(),
                "engagement_score": day2_engagement,
                "days_since_last_contact": day2_days_since_contact,
                "total_conversations": day2_total_conversations,
                "open_tickets": day2_open_tickets,
            }
        )

    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "customer_id",
                "event_timestamp",
                "engagement_score",
                "days_since_last_contact",
                "total_conversations",
                "open_tickets",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    # Parquet copy for Feast's FileSource. event_timestamp must be a real
    # timestamp dtype (not a string) for Feast's point-in-time join to work.
    df = pd.DataFrame(rows)
    df["event_timestamp"] = pd.to_datetime(df["event_timestamp"])
    df.to_parquet(PARQUET_PATH, index=False)

    print(f"Wrote {len(rows)} rows ({NUM_CUSTOMERS} customers x 2 snapshots) to {OUT_PATH} and {PARQUET_PATH}")


if __name__ == "__main__":
    main()
