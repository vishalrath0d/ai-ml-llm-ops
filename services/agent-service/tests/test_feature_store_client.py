"""Tests for app/feature_store_client.py -- the live Feast online-feature
lookup that feeds the urgency classifier real customer-context features.

Mocks the HTTP call to feature-store with unittest.mock (same pattern as
test_knowledge_base_tool.py) so no live feature-store service is required.
Response shapes here mirror exactly what a real `feast serve` instance
returns -- verified directly against a running feature-store container
during development, not guessed at.
"""
from __future__ import annotations

import httpx
from unittest.mock import MagicMock, patch

from app.feature_store_client import DEFAULT_FEATURES, get_customer_features


def _feast_response(values_by_name: dict) -> MagicMock:
    """Builds a fake feast-serve response. `values_by_name` maps feature
    name -> value (use None for a NOT_FOUND feature)."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "metadata": {"feature_names": list(values_by_name.keys())},
        "results": [
            {
                "values": [v] if v is not None else [None],
                "statuses": ["PRESENT" if v is not None else "NOT_FOUND"],
                "event_timestamps": ["2024-06-03T00:00:00Z"],
            }
            for v in values_by_name.values()
        ],
    }
    return resp


def test_returns_defaults_when_customer_id_is_none():
    result = get_customer_features(None)
    assert result == DEFAULT_FEATURES


def test_parses_a_present_customer_correctly():
    fake = _feast_response(
        {
            "customer_id": "cust_003",
            "engagement_score": 0.18,
            "total_conversations": 6,
            "days_since_last_contact": 22,
            "open_tickets": 3,
        }
    )
    with patch("app.feature_store_client.httpx.post", return_value=fake) as mock_post:
        result = get_customer_features("cust_003")

    assert result == {
        "engagement_score": 0.18,
        "days_since_last_contact": 22,
        "total_conversations": 6,
        "open_tickets": 3,
    }
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"]["entities"] == {"customer_id": ["cust_003"]}


def test_handles_feature_names_in_a_different_order_than_requested():
    """Verified directly against a real feast serve response: metadata.feature_names
    is NOT guaranteed to match the request's `features` order -- the parser
    must match by name, not position."""
    fake = _feast_response(
        {
            "customer_id": "cust_002",
            "total_conversations": 31,  # deliberately out of request order
            "engagement_score": 0.93,
            "open_tickets": 0,
            "days_since_last_contact": 1,
        }
    )
    with patch("app.feature_store_client.httpx.post", return_value=fake):
        result = get_customer_features("cust_002")

    assert result["engagement_score"] == 0.93
    assert result["total_conversations"] == 31


def test_returns_defaults_for_a_not_found_customer():
    fake = _feast_response(
        {
            "customer_id": "cust_999",
            "engagement_score": None,
            "total_conversations": None,
            "days_since_last_contact": None,
            "open_tickets": None,
        }
    )
    with patch("app.feature_store_client.httpx.post", return_value=fake):
        result = get_customer_features("cust_999")

    assert result == DEFAULT_FEATURES


def test_returns_defaults_when_feature_store_is_unreachable():
    with patch("app.feature_store_client.httpx.post", side_effect=httpx.ConnectError("boom")), \
         patch("app.feature_store_client.time.sleep"):
        result = get_customer_features("cust_002")

    assert result == DEFAULT_FEATURES


def test_returns_defaults_on_timeout():
    with patch("app.feature_store_client.httpx.post", side_effect=httpx.ReadTimeout("timed out")), \
         patch("app.feature_store_client.time.sleep"):
        result = get_customer_features("cust_002")

    assert result == DEFAULT_FEATURES


def test_succeeds_on_retry_after_one_transient_failure():
    """Under real host load, a single connection blip to feature-store
    should not be enough to make classify_urgency() fall back to neutral
    defaults for a customer who's actually on file -- this is the
    behavior a retry is specifically meant to cover."""
    fake = _feast_response(
        {"engagement_score": 0.93, "days_since_last_contact": 1, "total_conversations": 31, "open_tickets": 0}
    )
    with patch(
        "app.feature_store_client.httpx.post",
        side_effect=[httpx.ConnectError("transient blip"), fake],
    ) as mock_post, patch("app.feature_store_client.time.sleep") as mock_sleep:
        result = get_customer_features("cust_002")

    assert result == {
        "engagement_score": 0.93,
        "days_since_last_contact": 1,
        "total_conversations": 31,
        "open_tickets": 0,
    }
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once()
