"""Tests that the shared MCP tool server (app/mcp_server.py) exposes the
same two tools as the LangGraph agent, and that they delegate to the exact
same implementations in app/tools_impl.py (no duplicated integration code).
"""
from __future__ import annotations

import asyncio

from app.mcp_server import crm_lookup, mcp_server, search_knowledge_base
from app.tools_impl import crm_lookup_impl


def test_mcp_server_lists_both_tools():
    tools = asyncio.run(mcp_server.list_tools())
    names = {t.name for t in tools}
    assert names == {"search_knowledge_base", "crm_lookup"}


def test_mcp_crm_lookup_tool_delegates_to_shared_impl():
    # The MCP-decorated function is a thin wrapper; calling it directly
    # should return exactly what the shared implementation returns.
    assert crm_lookup("cust_002") == crm_lookup_impl("cust_002")


def test_mcp_call_tool_crm_lookup_round_trip():
    result = asyncio.run(mcp_server.call_tool("crm_lookup", {"identifier": "cust_001"}))
    # CallToolResult exposes structured/text content; assert the customer
    # name made it through the full MCP call path.
    text_blob = str(result)
    assert "Jane Doe" in text_blob


def test_search_knowledge_base_is_registered_and_callable(monkeypatch):
    monkeypatch.setattr(
        "app.mcp_server.search_knowledge_base_impl",
        lambda query: f"kb-result-for:{query}",
    )
    assert search_knowledge_base("refund policy") == "kb-result-for:refund policy"
