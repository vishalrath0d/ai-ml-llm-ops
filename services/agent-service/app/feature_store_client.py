"""HTTP client for the feature-store service's live online-feature lookups.

Feast serves online features over its own REST API (`feast serve`) rather
than agent-service importing feast in-process, matching this project's
established pattern of "every capability is its own HTTP service" (see
rag-service, llm-gateway) rather than every service growing a pile of
heavyweight ML library dependencies it doesn't otherwise need.

This is the piece that makes services/feature-store/ a REAL input to a live
model instead of a standalone lesson: app/model_registry.py calls
`get_customer_features()` on every /chat turn a customer_id is known for,
and feeds the result straight into the urgency classifier alongside the
message text (see services/mlflow/train_and_log.py for how the model was
trained to use both).

Defensive by design, same posture as model_registry.py and tracing.py:
feature-store being unreachable, a customer having no row on file, or a
missing customer_id must NEVER break /chat -- callers get neutral defaults
instead, and classification degrades to (mostly) text-only rather than
failing.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from app.config import FEATURE_STORE_URL

logger = logging.getLogger("agent-service.feature_store_client")

FEATURE_REFS = [
    "customer_engagement_features:engagement_score",
    "customer_engagement_features:days_since_last_contact",
    "customer_engagement_features:total_conversations",
    "customer_engagement_features:open_tickets",
]

# Neutral values -- chosen to not push the model toward either label, so an
# unreachable feature store or a not-yet-onboarded customer degrades toward
# text-only classification rather than skewing the result.
DEFAULT_FEATURES: dict = {
    "engagement_score": 0.5,
    "days_since_last_contact": 7,
    "total_conversations": 10,
    "open_tickets": 0,
}


def get_customer_features(customer_id: Optional[str], timeout_seconds: float = 5.0) -> dict:
    """Look up a customer's live engagement features from Feast's online
    store. Always returns a fully-populated dict with the same 4 keys as
    DEFAULT_FEATURES -- callers never need to check for missing keys."""
    if not customer_id:
        return dict(DEFAULT_FEATURES)

    try:
        # One retry after a short, fixed backoff on the network call
        # itself (not on parsing below) -- cheap cover for a transient
        # connection blip, same reasoning as tools_impl.py's identical
        # pattern for the rag-service call. Still falls through to the
        # broad `except Exception` -> DEFAULT_FEATURES below if both
        # attempts fail.
        try:
            resp = httpx.post(
                f"{FEATURE_STORE_URL}/get-online-features",
                json={"features": FEATURE_REFS, "entities": {"customer_id": [customer_id]}},
                timeout=timeout_seconds,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("feature-store call failed (attempt 1/2): %s -- retrying once", exc)
            time.sleep(0.5)
            resp = httpx.post(
                f"{FEATURE_STORE_URL}/get-online-features",
                json={"features": FEATURE_REFS, "entities": {"customer_id": [customer_id]}},
                timeout=timeout_seconds,
            )
            resp.raise_for_status()
        data = resp.json()

        # `metadata.feature_names` and `results` are parallel arrays, but
        # NOT necessarily in the order `features` was requested in (verified
        # directly against a running feast serve -- do not assume request
        # order). Zip them by name instead.
        feature_names = data["metadata"]["feature_names"]
        results = data["results"]
        by_name = {}
        for name, result in zip(feature_names, results):
            values = result.get("values") or []
            statuses = result.get("statuses") or []
            present = bool(values) and (not statuses or statuses[0] == "PRESENT")
            by_name[name] = values[0] if present and values else None

        if any(by_name.get(k) is None for k in DEFAULT_FEATURES):
            logger.info("customer_id=%s not found in feature store; using defaults", customer_id)
            return dict(DEFAULT_FEATURES)

        return {
            "engagement_score": float(by_name["engagement_score"]),
            "days_since_last_contact": int(by_name["days_since_last_contact"]),
            "total_conversations": int(by_name["total_conversations"]),
            "open_tickets": int(by_name["open_tickets"]),
        }
    except Exception:
        logger.exception("feature-store lookup failed for customer_id=%s; using defaults", customer_id)
        return dict(DEFAULT_FEATURES)
