"""Tests for the keyword-trigger routing table — no embedding model needed,
so these run fast even without the sentence-transformers download."""
from app.retrieval import match_keyword_trigger


def test_billing_keyword_routes_to_billing_doc():
    match = match_keyword_trigger("How do I update my billing information?")
    assert match is not None
    keyword, doc_id = match
    assert doc_id == "doc_billing"
    assert "bill" in keyword


def test_password_reset_routes_to_login_doc():
    match = match_keyword_trigger("I forgot my password, please help!")
    assert match is not None
    _, doc_id = match
    assert doc_id == "doc_login_troubleshooting"


def test_case_insensitive_matching():
    match = match_keyword_trigger("Need help with my API KEY for the REST API")
    assert match is not None
    _, doc_id = match
    assert doc_id == "doc_api_tickets"


def test_unmatched_query_returns_none():
    assert match_keyword_trigger("What is the meaning of life, the universe, and everything?") is None
