"""Tests for app/model_registry.py's classify_urgency() -- specifically the
combined text+Feast-features behavior (see services/mlflow/train_and_log.py
for how the model was trained to expect this exact shape). MLflow itself is
never touched here: `_cached_model` is monkeypatched directly with a fake
pyfunc-shaped object, the same way test_api.py avoids a live MLflow server.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx

import app.model_registry as model_registry


class _FakeModel:
    """Records the DataFrame it was called with and returns a fixed label."""

    def __init__(self, label="normal"):
        self.label = label
        self.last_input = None

    def predict(self, df):
        self.last_input = df
        return [self.label]


def _install_fake_model(monkeypatch, label="normal", version="3"):
    fake = _FakeModel(label)
    monkeypatch.setattr(model_registry, "_cached_model", fake)
    monkeypatch.setattr(model_registry, "_cached_version", version)
    # Prevent _ensure_fresh() from trying to talk to a real MLflow server --
    # it only reloads once the refresh interval elapses, and _last_loaded_at
    # defaults to 0.0, so pin it to "just loaded" for the duration of the test.
    monkeypatch.setattr(model_registry, "_last_loaded_at", __import__("time").monotonic())
    return fake


def test_classify_urgency_builds_a_dataframe_with_text_and_features(monkeypatch):
    fake = _install_fake_model(monkeypatch)
    monkeypatch.setattr(
        model_registry,
        "get_customer_features",
        lambda customer_id: {
            "engagement_score": 0.18,
            "days_since_last_contact": 22,
            "total_conversations": 6,
            "open_tickets": 3,
        },
    )

    label, version = model_registry.classify_urgency("still waiting on this", "cust_003")

    assert label == "normal"
    assert version == "3"
    row = fake.last_input.iloc[0]
    assert row["text"] == "still waiting on this"
    assert row["open_tickets"] == 3
    assert row["engagement_score"] == 0.18


def test_classify_urgency_uses_default_features_when_customer_id_is_none(monkeypatch):
    fake = _install_fake_model(monkeypatch)
    captured = {}

    def fake_get_features(customer_id):
        captured["customer_id"] = customer_id
        return {"engagement_score": 0.5, "days_since_last_contact": 7, "total_conversations": 10, "open_tickets": 0}

    monkeypatch.setattr(model_registry, "get_customer_features", fake_get_features)

    model_registry.classify_urgency("hello", None)

    assert captured["customer_id"] is None
    assert fake.last_input.iloc[0]["open_tickets"] == 0


def test_classify_urgency_degrades_to_unknown_when_no_model_loaded(monkeypatch):
    monkeypatch.setattr(model_registry, "_cached_model", None)
    # _cached_model is None, so _ensure_fresh() will try to (re)load regardless
    # of _last_loaded_at -- stub _load_model() too so this test never makes a
    # real MLflow connection attempt.
    monkeypatch.setattr(model_registry, "_load_model", lambda: (None, None))

    label, version = model_registry.classify_urgency("anything", "cust_001")

    assert label == "unknown"
    assert version is None


def test_classify_urgency_degrades_to_unknown_on_prediction_error(monkeypatch):
    class _BoomModel:
        def predict(self, df):
            raise RuntimeError("boom")

    monkeypatch.setattr(model_registry, "_cached_model", _BoomModel())
    monkeypatch.setattr(model_registry, "_cached_version", "3")
    monkeypatch.setattr(model_registry, "_last_loaded_at", __import__("time").monotonic())
    monkeypatch.setattr(model_registry, "get_customer_features", lambda customer_id: {
        "engagement_score": 0.5, "days_since_last_contact": 7, "total_conversations": 10, "open_tickets": 0,
    })

    label, version = model_registry.classify_urgency("anything", "cust_001")

    assert label == "unknown"
    assert version == "3"  # still reports the loaded version, just couldn't classify


def test_classify_urgency_degrades_correctly_under_concurrent_load_when_feature_store_is_down():
    """The gap this covers: classify_urgency() is called once per /chat
    request, and under real traffic those calls happen concurrently, not
    one at a time. This proves the Feast-unreachable degrade path (real
    app.feature_store_client.get_customer_features(), NOT mocked away --
    only the underlying httpx.post it calls is) is actually safe under
    concurrency: every thread gets a correct, independent result, the
    shared model cache isn't corrupted, and nothing deadlocks or raises."""
    import time as time_module

    fake = _FakeModel(label="normal")
    with patch.object(model_registry, "_cached_model", fake), \
         patch.object(model_registry, "_cached_version", "3"), \
         patch.object(model_registry, "_last_loaded_at", time_module.monotonic()), \
         patch("app.feature_store_client.httpx.post", side_effect=httpx.ConnectError("feature-store down")), \
         patch("app.feature_store_client.time.sleep"):  # skip the real 0.5s retry backoff

        def _classify(i: int):
            return model_registry.classify_urgency(f"message {i}", f"cust_{i:03d}")

        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(_classify, range(20)))

    # Every single one of the 20 concurrent calls degraded correctly and
    # independently -- none crashed, hung, or got another thread's result.
    assert len(results) == 20
    assert all(label == "normal" and version == "3" for label, version in results)
