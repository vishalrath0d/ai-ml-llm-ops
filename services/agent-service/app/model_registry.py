"""Live MLflow Model Registry integration — the actual observable MLOps
piece of this project.

agent-service loads whichever model version is tagged with the `champion`
alias for `support-urgency-classifier` in MLflow's registry, uses it to
classify each incoming chat message's urgency, and caches that in-process
with a periodic refresh. That means promoting or rolling back the alias in
MLflow (see services/mlflow/train_and_log.py) changes THIS service's live
behavior — without a redeploy, a restart, or touching a single line of
code here. That live-effect property is the entire point of a model
registry; see README.md for the concrete before/after exercise.

The model itself combines message TEXT with LIVE customer-context features
pulled from Feast's online store (see app/feature_store_client.py) — the
same "urgent"-sounding message classifies differently depending on which
customer sent it, e.g. an at-risk customer with several open tickets tips a
borderline message toward "urgent" where a healthy customer's identical
message stays "normal". See services/feature-store/README.md.

Defensive by design: MLflow being unreachable, no model registered yet, or
a bad model artifact must NEVER break /chat. Every failure degrades to
label "unknown" and logs at INFO (not ERROR/exception spam), the same
posture app/tracing.py already takes for Langfuse.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from app.config import (
    MLFLOW_TRACKING_URI,
    MODEL_REFRESH_SECONDS,
    URGENCY_MODEL_ALIAS,
    URGENCY_MODEL_NAME,
)
from app.feature_store_client import get_customer_features
from app.metrics import URGENCY_MODEL_VERSION_LOADED

logger = logging.getLogger("agent-service.model_registry")

_lock = threading.Lock()
_cached_model: Any = None
_cached_version: Optional[str] = None
_last_loaded_at: float = 0.0


def _load_model() -> tuple[Any, Optional[str]]:
    """Load the current @alias version from MLflow. Never raises — returns
    (None, None) on any failure (unreachable server, model/alias doesn't
    exist yet, bad artifact) so callers always have a safe fallback."""
    try:
        import mlflow

        mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
        model_uri = f"models:/{URGENCY_MODEL_NAME}@{URGENCY_MODEL_ALIAS}"
        model = mlflow.pyfunc.load_model(model_uri)

        from mlflow.tracking import MlflowClient

        client = MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)
        mv = client.get_model_version_by_alias(URGENCY_MODEL_NAME, URGENCY_MODEL_ALIAS)
        return model, mv.version
    except Exception as exc:  # noqa: BLE001 - registry issues must never break /chat
        logger.info(
            "Could not load %s@%s from MLflow at %s (%s) - urgency classification "
            "degraded to 'unknown'. This is expected until you've run "
            "services/mlflow/train_and_log.py at least once.",
            URGENCY_MODEL_NAME, URGENCY_MODEL_ALIAS, MLFLOW_TRACKING_URI, exc,
        )
        return None, None


def _ensure_fresh() -> None:
    global _cached_model, _cached_version, _last_loaded_at
    now = time.monotonic()
    with _lock:
        if _cached_model is not None and (now - _last_loaded_at) < MODEL_REFRESH_SECONDS:
            return
        model, version = _load_model()
        if model is not None:
            if version != _cached_version:
                logger.info(
                    "Loaded %s version=%s (alias=%s)%s",
                    URGENCY_MODEL_NAME, version, URGENCY_MODEL_ALIAS,
                    "" if _cached_version is None else f" (was version={_cached_version})",
                )
            _cached_model = model
            _cached_version = version
            URGENCY_MODEL_VERSION_LOADED.set(float(version))
        _last_loaded_at = now


def force_reload() -> dict[str, Any]:
    """Force an immediate reload, bypassing the refresh-interval cache.
    Backs POST /admin/reload-model, so a demo doesn't have to wait for the
    next natural refresh window after flipping an alias in MLflow."""
    global _cached_model, _cached_version, _last_loaded_at
    with _lock:
        model, version = _load_model()
        _cached_model = model
        _cached_version = version
        _last_loaded_at = time.monotonic()
        if version is not None:
            URGENCY_MODEL_VERSION_LOADED.set(float(version))
    return {"loaded": model is not None, "version": version}


def classify_urgency(text: str, customer_id: Optional[str] = None) -> tuple[str, Optional[str]]:
    """Returns (label, model_version).

    label is "urgent" / "normal" (whatever the registered model actually
    predicts) or "unknown" if no model is currently loaded — never raises.

    When `customer_id` is given, this pulls that customer's LIVE engagement
    features from Feast's online store (see app/feature_store_client.py)
    and feeds them to the model alongside the message text — the model was
    trained on exactly this combined shape (see
    services/mlflow/train_and_log.py). Feature-store lookup failures never
    propagate here; a missing/unreachable customer_id just degrades to
    neutral default features, not a broken classification.
    """
    _ensure_fresh()
    if _cached_model is None:
        return "unknown", None
    try:
        import pandas as pd

        features = get_customer_features(customer_id)
        row = pd.DataFrame([{"text": text, **features}])
        prediction = _cached_model.predict(row)
        label = str(prediction[0])
        return label, _cached_version
    except Exception:  # noqa: BLE001 - a bad prediction must never break /chat
        logger.exception("classify_urgency prediction failed; degrading to 'unknown'")
        return "unknown", _cached_version


def current_status() -> dict[str, Any]:
    """Snapshot for GET /admin/reload-model or debugging: what's loaded right
    now, without forcing a reload."""
    return {
        "model_name": URGENCY_MODEL_NAME,
        "alias": URGENCY_MODEL_ALIAS,
        "loaded_version": _cached_version,
        "last_loaded_monotonic": _last_loaded_at,
        "refresh_interval_seconds": MODEL_REFRESH_SECONDS,
    }
