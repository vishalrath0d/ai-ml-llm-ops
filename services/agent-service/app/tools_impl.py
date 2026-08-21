"""Pure-python implementations of the agent's tools.

These functions contain NO LangChain / MCP framework code. They are wrapped
twice, from two different framework adapters, so the integration logic lives
in exactly one place:

  * `app/graph.py`   wraps them with `@langchain_core.tools.tool` so the
                      LangGraph agent can call them as LangChain tools.
  * `app/mcp_server.py` wraps them with `@FastMCP.tool()` so they are also
                      reachable by ANY external MCP client (Claude Desktop,
                      the MCP inspector, another team's agent, ...) over the
                      shared MCP server exposed at /mcp.

This mirrors a common shared MCP tool server pattern: one implementation,
multiple attach points, no duplicated integration code per AI product.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import RAG_SERVICE_URL

logger = logging.getLogger("agent-service.tools")

# ---------------------------------------------------------------------------
# crm_lookup - mocked in-memory CRM
#
# Mirrors the role of a typical internal CRM client library: a
# tool the agent can call to fetch account/customer context by phone or
# email. Here it's a small in-memory dict instead of a real CRM API call, so
# this project can run fully offline.
# ---------------------------------------------------------------------------

_MOCK_CRM_DB: dict[str, dict[str, Any]] = {
    "cust_001": {
        "customer_id": "cust_001",
        "name": "Jane Doe",
        "phone": "+15551234567",
        "email": "jane.doe@example.com",
        "plan": "Pro",
        "account_status": "active",
        "open_tickets": 1,
        "last_contact": "2026-08-10",
    },
    "cust_002": {
        "customer_id": "cust_002",
        "name": "Amit Shah",
        "phone": "+15559876543",
        "email": "amit.shah@example.com",
        "plan": "Enterprise",
        "account_status": "active",
        "open_tickets": 0,
        "last_contact": "2026-07-28",
    },
    "cust_003": {
        "customer_id": "cust_003",
        "name": "Maria Gomez",
        "phone": "+15550001111",
        "email": "maria.gomez@example.com",
        "plan": "Free",
        "account_status": "past_due",
        "open_tickets": 3,
        "last_contact": "2026-08-15",
    },
}

# Secondary indexes so lookups by phone or email are O(1) as well.
_CRM_BY_PHONE: dict[str, str] = {rec["phone"]: cid for cid, rec in _MOCK_CRM_DB.items()}
_CRM_BY_EMAIL: dict[str, str] = {rec["email"].lower(): cid for cid, rec in _MOCK_CRM_DB.items()}


def crm_lookup_impl(identifier: Any) -> dict[str, Any]:
    """Look up a mocked customer record by phone number, email, or customer_id.

    Args:
        identifier: A phone number (e.g. "+15551234567"), an email address,
            or a customer_id (e.g. "cust_001"). Normally a plain string, but
            small local models occasionally hand back a nested dict for a
            single-string-argument tool (e.g. {"identifier": "cust_001"}) --
            tolerate that shape too instead of crashing the tool call.
    """
    if isinstance(identifier, dict):
        identifier = next(
            (v for v in identifier.values() if isinstance(v, str)),
            str(identifier),
        )
    key = str(identifier).strip()
    customer_id = (
        _CRM_BY_PHONE.get(key)
        or _CRM_BY_EMAIL.get(key.lower())
        or (key if key in _MOCK_CRM_DB else None)
    )
    if customer_id is None:
        logger.info("crm_lookup miss for identifier=%r", identifier)
        return {"found": False, "identifier": identifier}

    record = dict(_MOCK_CRM_DB[customer_id])
    record["found"] = True
    return record


# ---------------------------------------------------------------------------
# search_knowledge_base - proxies to the sibling rag-service
# ---------------------------------------------------------------------------


def search_knowledge_base_impl(query: str, timeout_seconds: float = 15.0) -> str:
    """Query the sibling rag-service's knowledge base for relevant context.

    Args:
        query: The natural-language question to search the knowledge base for.
        timeout_seconds: HTTP timeout for the call to rag-service.

    Returns:
        A short text blob of retrieved context, or a descriptive error string
        if rag-service could not be reached / returned an error. Errors are
        returned as strings (not raised) so the LLM can see the failure and
        decide how to proceed, rather than crashing the graph.
    """
    # One retry after a short, fixed backoff -- not a general resilience
    # library, just a cheap cover for the one failure mode a retry
    # actually helps with (a transient connection blip), before degrading
    # to the "knowledge base unavailable" string below. A second retry
    # would just double the wait for a failure mode (rag-service actually
    # down, or a real timeout under host load) a retry can't fix anyway.
    last_exc: httpx.HTTPError | None = None
    response = None
    for attempt in range(2):
        try:
            response = httpx.post(
                RAG_SERVICE_URL,
                json={"query": query},
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            last_exc = None
            break
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt == 0:
                logger.warning("rag-service call failed (attempt 1/2): %s -- retrying once", exc)
                time.sleep(0.5)
    if last_exc is not None:
        logger.warning("rag-service call failed: %s", last_exc)
        return f"knowledge base unavailable ({last_exc.__class__.__name__}): {last_exc}"
    try:
        data = response.json()
    except ValueError as exc:  # pragma: no cover - defensive, non-JSON body
        logger.warning("rag-service returned non-JSON body: %s", exc)
        return "knowledge base returned an unparseable response"

    # rag-service's response shape is owned by that sibling service; be
    # liberal about what we accept so this tool degrades gracefully instead
    # of raising if the exact schema drifts.
    if isinstance(data, dict):
        if "answer" in data:
            return str(data["answer"])
        if "results" in data:
            return str(data["results"])
        if "context" in data:
            return str(data["context"])
    return str(data)
