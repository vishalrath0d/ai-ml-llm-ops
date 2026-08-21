"""Tests for the search_knowledge_base tool's HTTP call to rag-service.

Mocks the rag-service HTTP call with unittest.mock so no live rag-service is
required.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools_impl import search_knowledge_base_impl


def test_search_knowledge_base_returns_answer_field():
    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"answer": "Reset your password from Settings > Security."}

    with patch("app.tools_impl.httpx.post", return_value=fake_response) as mock_post:
        result = search_knowledge_base_impl("how do I reset my password?")

    assert result == "Reset your password from Settings > Security."
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {"query": "how do I reset my password?"}


def test_search_knowledge_base_falls_back_to_results_field():
    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"results": ["doc1", "doc2"]}

    with patch("app.tools_impl.httpx.post", return_value=fake_response):
        result = search_knowledge_base_impl("pricing plans")

    assert result == "['doc1', 'doc2']"


def test_search_knowledge_base_degrades_gracefully_on_http_error():
    import httpx

    with patch("app.tools_impl.httpx.post", side_effect=httpx.ConnectError("boom")), \
         patch("app.tools_impl.time.sleep"):
        result = search_knowledge_base_impl("anything")

    assert "knowledge base unavailable" in result
    assert "ConnectError" in result


def test_search_knowledge_base_succeeds_on_retry_after_one_transient_failure():
    import httpx

    fake_response = MagicMock()
    fake_response.raise_for_status.return_value = None
    fake_response.json.return_value = {"answer": "recovered on retry"}

    with patch(
        "app.tools_impl.httpx.post",
        side_effect=[httpx.ConnectError("transient blip"), fake_response],
    ) as mock_post, patch("app.tools_impl.time.sleep") as mock_sleep:
        result = search_knowledge_base_impl("anything")

    assert result == "recovered on retry"
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once()
