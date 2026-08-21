"""The LangGraph agent: an explicit state graph replacing hand-rolled
coordinator/sub-agent routing.

In a lot of production systems, a coordinator built on classic LangChain
`AgentExecutor` decides tool-vs-respond by running a `while` loop around the
executor, and prevents infinite tool<->LLM ping-pong with manually written
iteration counters and "have we seen this exact tool call before" guards
scattered through the coordinator code.

Here the same behavior is expressed as an explicit graph with four nodes and
one designed cycle:

    START -> router --(tool call: search_knowledge_base)--> search_knowledge_base --> router
                    --(tool call: crm_lookup)------------> crm_lookup --> router
                    --(no tool call, or loop guard tripped)--> respond --> END

The loop guard is a single `state["iterations"]` counter incremented once per
`router` visit and checked in `route_after_router` - one declarative
conditional edge, in one place, instead of ad-hoc checks sprinkled through
imperative coordinator code.
"""
from __future__ import annotations

import logging
from typing import Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from app.config import (
    LLM_GATEWAY_API_KEY,
    LLM_GATEWAY_URL,
    LLM_MODEL,
    LLM_REQUEST_TIMEOUT_SECONDS,
    MAX_ITERATIONS,
)
from app.tools_impl import crm_lookup_impl, search_knowledge_base_impl

logger = logging.getLogger("agent-service.graph")

SYSTEM_PROMPT = (
    "You are a customer support agent for Onwly. "
    "Use the `crm_lookup` tool when the user references their account, "
    "phone number, email, or customer id and you need account context. "
    "Use the `search_knowledge_base` tool when the user asks a product, "
    "billing policy, or how-to question you don't already know the answer "
    "to from this conversation. "
    "If a tool result already answers the question, respond directly - do "
    "not call the same tool again with the same input. "
    "Keep answers concise and friendly."
)


# ---------------------------------------------------------------------------
# LangChain tool wrappers around the shared implementations in tools_impl.py
# ---------------------------------------------------------------------------


@tool
def search_knowledge_base(query: str) -> str:
    """Search the product/support knowledge base for information relevant to
    the user's question. Use this for product, billing, or how-to questions.
    """
    return search_knowledge_base_impl(query)


@tool
def crm_lookup(identifier: str) -> dict[str, Any]:
    """Look up a customer's CRM record by phone number, email, or customer_id.
    Use this when you need account status, plan, or ticket context.
    """
    return crm_lookup_impl(identifier)


TOOLS = [search_knowledge_base, crm_lookup]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    session_id: str
    messages: list[BaseMessage]
    tool_calls: list[str]  # names of tools invoked this turn, in order
    iterations: int
    response: str


def _build_llm(bind_tools: bool) -> ChatOpenAI:
    """Build a ChatOpenAI client pointed at the internal llm-gateway.

    This is the ONLY place an LLM client is constructed in this service. It
    is deliberately routed at LLM_GATEWAY_URL, never at a real provider - see
    the README "Critical constraint" section.
    """
    llm = ChatOpenAI(
        model=LLM_MODEL,
        api_key=LLM_GATEWAY_API_KEY,
        base_url=LLM_GATEWAY_URL.removesuffix("/chat/completions"),
        timeout=LLM_REQUEST_TIMEOUT_SECONDS,
        temperature=0,
        # max_retries=0 is not decorative -- the openai SDK's default (2
        # retries) means every failed call gets retried HERE, on top of
        # llm-gateway's own N-deep provider fallback chain, which already
        # tried every configured provider before returning an error.
        # Observed directly: a single /chat request that fell through all
        # 4 providers (their own fast auth failures plus one slow Ollama
        # timeout) then got retried by THIS client 1-2 more times, each
        # retry re-walking the entire chain from scratch and multiplying
        # total latency several times over -- for a failure class (auth,
        # config, a provider genuinely down) a bare retry is very unlikely
        # to fix. llm-gateway is the layer that already owns retrying via
        # fallback; this client retrying on top of that is pure waste.
        max_retries=0,
    )
    if bind_tools:
        # parallel_tool_calls=False keeps exactly one tool call per AI turn,
        # which keeps the graph's routing (one tool name -> one node) simple
        # and avoids having to fan a single AI turn out across both tool
        # nodes before the model is allowed to see any results.
        return llm.bind_tools(TOOLS, parallel_tool_calls=False)
    return llm


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def router_node(state: AgentState) -> dict[str, Any]:
    """Ask the LLM (via llm-gateway) whether to call a tool or respond."""
    llm = _build_llm(bind_tools=True)
    ai_message = llm.invoke(state["messages"])
    logger.debug("router iteration=%s tool_calls=%s", state["iterations"] + 1, getattr(ai_message, "tool_calls", None))
    return {
        "messages": state["messages"] + [ai_message],
        "iterations": state["iterations"] + 1,
    }


def _make_tool_node(tool_name: str):
    """Build a node function that executes a single named tool.

    Both tool nodes share this shape: pull the pending tool_call for
    `tool_name` off the last AIMessage, run it, append a ToolMessage with the
    result, and record the invocation in state["tool_calls"] for the
    /chat response's observability payload.
    """

    def _node(state: AgentState) -> dict[str, Any]:
        last_message = state["messages"][-1]
        assert isinstance(last_message, AIMessage) and last_message.tool_calls
        tool_call = next(
            (tc for tc in last_message.tool_calls if tc["name"] == tool_name),
            last_message.tool_calls[0],
        )
        langchain_tool = TOOLS_BY_NAME[tool_name]
        try:
            result = langchain_tool.invoke(tool_call["args"])
        except Exception as exc:  # noqa: BLE001 - surface tool errors to the LLM
            logger.exception("tool %s raised", tool_name)
            result = f"tool {tool_name} raised an error: {exc}"

        tool_message = ToolMessage(
            content=str(result),
            tool_call_id=tool_call["id"],
            name=tool_name,
        )
        return {
            "messages": state["messages"] + [tool_message],
            "tool_calls": state["tool_calls"] + [tool_name],
        }

    _node.__name__ = f"{tool_name}_node"
    return _node


search_knowledge_base_node = _make_tool_node("search_knowledge_base")
crm_lookup_node = _make_tool_node("crm_lookup")


def respond_node(state: AgentState) -> dict[str, Any]:
    """Produce the final natural-language answer for this turn.

    In the common case the router's last AIMessage already IS the final
    answer (it had no tool_calls), so we just surface its content. If we
    landed here because the loop guard tripped while the model still wanted
    to call a tool, we make one final untooled LLM call so the user still
    gets a real answer instead of an error.
    """
    last_message = state["messages"][-1]

    if isinstance(last_message, AIMessage) and not last_message.tool_calls:
        return {"response": last_message.content}

    logger.warning(
        "respond_node reached with a pending tool call after %s iterations; "
        "forcing a final untooled answer (loop guard).",
        state["iterations"],
    )
    guard_notice = SystemMessage(
        content=(
            "You have reached the maximum number of tool calls for this "
            "turn. Answer the user's question as best you can with the "
            "information already gathered, without calling any more tools."
        )
    )
    llm = _build_llm(bind_tools=False)
    final_message = llm.invoke(state["messages"] + [guard_notice])
    return {
        "messages": state["messages"] + [guard_notice, final_message],
        "response": final_message.content,
    }


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------

RouteDecision = Literal["search_knowledge_base", "crm_lookup", "respond"]


def route_after_router(state: AgentState) -> RouteDecision:
    """The graph's single loop-guard + tool-dispatch decision point.

    This one function is the explicit equivalent of the manual
    "anti-ping-pong" checks used in a lot of production systems: it is the only place that decides whether to
    keep looping or to force termination.
    """
    if state["iterations"] >= MAX_ITERATIONS:
        return "respond"

    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        tool_name = last_message.tool_calls[0]["name"]
        if tool_name in ("search_knowledge_base", "crm_lookup"):
            return tool_name  # type: ignore[return-value]
        logger.warning("router requested unknown tool %r; responding instead", tool_name)
    return "respond"


def build_graph() -> Any:
    """Assemble and compile the LangGraph state graph."""
    graph = StateGraph(AgentState)

    graph.add_node("router", router_node)
    graph.add_node("search_knowledge_base", search_knowledge_base_node)
    graph.add_node("crm_lookup", crm_lookup_node)
    graph.add_node("respond", respond_node)

    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router",
        route_after_router,
        {
            "search_knowledge_base": "search_knowledge_base",
            "crm_lookup": "crm_lookup",
            "respond": "respond",
        },
    )
    # The cycle: after running a tool, go back to the router so it can
    # decide whether another tool call is needed or it's time to respond.
    graph.add_edge("search_knowledge_base", "router")
    graph.add_edge("crm_lookup", "router")
    graph.add_edge("respond", END)

    return graph.compile()


# Compiled once at import time and reused across requests (stateless graph;
# per-conversation state lives outside the graph, see app/main.py).
COMPILED_GRAPH = build_graph()


def run_agent_turn(
    session_id: str,
    history: list[BaseMessage],
    user_message: str,
    extra_system_note: str | None = None,
) -> tuple[str, list[str], list[BaseMessage]]:
    """Run one /chat turn through the compiled graph.

    Args:
        session_id: Caller-provided conversation identifier.
        history: Prior messages for this session (empty for a new session).
        user_message: The new user message for this turn.
        extra_system_note: Optional one-off SystemMessage injected just
            before the user's message this turn only (not persisted into
            history). Used by app/main.py to inject a live MLflow-driven
            urgency hint — see app/model_registry.py — without this graph
            needing to know anything about MLflow itself. Purely additive:
            omitting it (the default) reproduces the exact prior behavior.

    Returns:
        (response_text, tool_calls_invoked, updated_history)
    """
    from langchain_core.messages import HumanMessage

    messages: list[BaseMessage] = list(history)
    if not messages:
        messages.append(SystemMessage(content=SYSTEM_PROMPT))
    if extra_system_note:
        messages.append(SystemMessage(content=extra_system_note))
    messages.append(HumanMessage(content=user_message))

    initial_state: AgentState = {
        "session_id": session_id,
        "messages": messages,
        "tool_calls": [],
        "iterations": 0,
        "response": "",
    }

    final_state: AgentState = COMPILED_GRAPH.invoke(initial_state)
    return final_state["response"], final_state["tool_calls"], final_state["messages"]
