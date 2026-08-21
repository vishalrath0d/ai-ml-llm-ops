"""Tests for the mocked in-memory CRM tool (app/tools_impl.py).

Mirrors a typical internal CRM client library's role: given a phone,
email, or customer_id, return account context for the agent to reason
over.
"""
from __future__ import annotations

from app.tools_impl import crm_lookup_impl


def test_lookup_by_customer_id_hits():
    result = crm_lookup_impl("cust_001")
    assert result["found"] is True
    assert result["customer_id"] == "cust_001"
    assert result["name"] == "Jane Doe"
    assert result["plan"] == "Pro"


def test_lookup_by_phone_hits():
    result = crm_lookup_impl("+15559876543")
    assert result["found"] is True
    assert result["customer_id"] == "cust_002"
    assert result["name"] == "Amit Shah"


def test_lookup_by_email_is_case_insensitive():
    result = crm_lookup_impl("MARIA.GOMEZ@example.com")
    assert result["found"] is True
    assert result["customer_id"] == "cust_003"
    assert result["account_status"] == "past_due"


def test_lookup_trims_whitespace():
    result = crm_lookup_impl("  cust_002  ")
    assert result["found"] is True
    assert result["customer_id"] == "cust_002"


def test_lookup_miss_returns_not_found_shape():
    result = crm_lookup_impl("not-a-real-customer")
    assert result == {"found": False, "identifier": "not-a-real-customer"}


def test_lookup_does_not_leak_between_records():
    jane = crm_lookup_impl("cust_001")
    amit = crm_lookup_impl("cust_002")
    assert jane["email"] != amit["email"]
    assert jane["customer_id"] != amit["customer_id"]


def test_lookup_tolerates_a_dict_shaped_identifier():
    """qwen2.5:0.5b (the default local model) sometimes hands back a nested
    dict instead of a plain string for this tool's single string argument,
    e.g. {"identifier": {"customer_id": "cust_001", "tool": "crm_lookup"}}
    -- confirmed against the real running model, not a hypothetical. The
    tool should still resolve the customer instead of crashing on
    `identifier.strip()`."""
    result = crm_lookup_impl({"customer_id": "cust_001", "tool": "crm_lookup"})
    assert result["found"] is True
    assert result["customer_id"] == "cust_001"
