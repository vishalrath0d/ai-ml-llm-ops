"""Shared MCP tool server for agent-service.

Exposes the SAME tool implementations the LangGraph agent uses (see
app/tools_impl.py) as MCP tools, over SSE transport, so any MCP-speaking
client - the MCP inspector, Claude Desktop, another team's agent - can call
`search_knowledge_base` and `crm_lookup` without going through this
service's LangGraph agent or its /chat endpoint at all.

This is the concrete, single-service demonstration of a common
pattern: see README.md for how production conversational-AI and voice-AI backends both
attach to ONE shared MCP tool server instead of each re-implementing CRM and
knowledge-base integrations.

Uses the official `mcp` Python SDK. NOTE: as installed in this project, the
SDK's ergonomic decorator-based server class is `mcp.server.mcpserver.MCPServer`
(the "FastMCP-style" API referenced in this service's spec/README - some SDK
versions ship this same class under the older name `FastMCP`; the decorator
API (`@mcp.tool()`, `.sse_app()`) is what matters and is stable across both
names).
"""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from app.tools_impl import crm_lookup_impl, search_knowledge_base_impl

mcp_server = MCPServer(
    name="agent-service-tools",
    description=(
        "Shared knowledge-base search and CRM lookup tools for AI agents. "
        "Mirrors a common shared MCP tool server pattern."
    ),
)


@mcp_server.tool()
def search_knowledge_base(query: str) -> str:
    """Search the product/support knowledge base for information relevant to a question.

    Args:
        query: The natural-language question to search for.
    """
    return search_knowledge_base_impl(query)


@mcp_server.tool()
def crm_lookup(identifier: str) -> dict[str, Any]:
    """Look up a customer's CRM record by phone number, email, or customer_id.

    Args:
        identifier: A phone number, email address, or customer_id.
    """
    return crm_lookup_impl(identifier)


# Starlette ASGI app implementing the MCP SSE transport (GET /sse to open the
# event stream, POST /messages/ for the client->server leg). Mounted at
# "/mcp" in app/main.py, so the full paths are /mcp/sse and /mcp/messages/.
#
# host="0.0.0.0" (rather than the SDK default "127.0.0.1") disables the
# SDK's automatic localhost-only DNS-rebinding protection, since this app is
# meant to be reached over the docker-compose network by hostname, not just
# from localhost.
mcp_asgi_app = mcp_server.sse_app(host="0.0.0.0")
