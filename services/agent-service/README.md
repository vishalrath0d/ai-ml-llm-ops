# agent-service

A conversational agent microservice built on **LangGraph**, demonstrating a
production agentic tool-calling pattern - agents call a
shared external tool/knowledge-base server over MCP - but with an explicit
state graph in place of hand-rolled coordinator/sub-agent routing.

- Port: **8003**
- Framework: FastAPI + LangGraph + LangChain (`ChatOpenAI` pointed at an
  internal gateway, never a real provider)
- Also exposes its tools as a standalone **MCP server** over SSE at `/mcp`

## Why this exists: LangGraph vs. a hand-rolled AgentExecutor

In a lot of production systems, a coordinator is built on classic
LangChain `AgentExecutor`. `AgentExecutor` runs a bare `while` loop:
ask the LLM, if it wants a tool call the tool, feed the result back, ask
again - repeat. The *shape* of that loop (when to stop, which sub-agent or
tool to defer to, how to stop two tools from calling each other forever)
lives in **imperative Python written by hand**: a `max_iterations` kwarg,
plus extra "anti-ping-pong" bookkeeping the coordinator team added on top
(tracking recent tool-call signatures, bailing out if the same tool is
called with the same arguments twice in a row, etc.). That logic is
correct, but it's scattered, implicit in control flow, and easy to get
subtly wrong when a new tool or sub-agent is added.

This service expresses the exact same behavior as an explicit **state
graph** instead. Here is the actual graph built in `app/graph.py`:

```
START -> router --(tool call: search_knowledge_base)--> search_knowledge_base --> router
                --(tool call: crm_lookup)------------> crm_lookup --> router
                --(no tool call, OR loop guard tripped)--> respond --> END
```

Four nodes:

- **`router`** - calls the LLM (via llm-gateway, tools bound) and appends
  its response to the conversation. This is the *only* place a
  tool-vs-respond decision gets made.
- **`search_knowledge_base`** - a tool node that calls the sibling
  `rag-service`.
- **`crm_lookup`** - a tool node that queries the mocked in-memory CRM.
- **`respond`** - turns the final AI message into the `/chat` response, or
  (if the loop guard tripped) makes one last untooled LLM call so the user
  still gets a real answer.

The cycle (`router -> tool -> router`) is a first-class, declared edge in
the graph, not an implicit `while` loop. The loop guard is **one**
function, `route_after_router` in `app/graph.py`:

```python
def route_after_router(state: AgentState) -> RouteDecision:
    if state["iterations"] >= MAX_ITERATIONS:
        return "respond"
    last_message = state["messages"][-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        tool_name = last_message.tool_calls[0]["name"]
        if tool_name in ("search_knowledge_base", "crm_lookup"):
            return tool_name
    return "respond"
```

That's the entire "anti-ping-pong" guard: one counter (`state["iterations"]`),
incremented once per `router` visit, checked in one conditional edge. There
is no separate bookkeeping of "have I seen this tool call before" scattered
through coordinator code - the graph structure itself makes ping-ponging
between two tools impossible to run away forever, because *every* path back
into `router` passes through this same edge. Whoever reads `app/graph.py`
can see the entire control flow - every state the conversation can be in,
and every transition out of it - as data (the `add_node`/`add_conditional_edges`
calls), not as something you have to trace through nested `if`s and `while`s
to reconstruct.

`MAX_ITERATIONS` defaults to 5 (env var `MAX_ITERATIONS`).

## Critical constraint: no direct LLM provider calls

This service **never** calls OpenAI, Anthropic, or Ollama directly. The only
LLM entrypoint it knows about is the sibling `llm-gateway` service's
OpenAI-compatible endpoint (`LLM_GATEWAY_URL`, default
`http://llm-gateway:8001/v1/chat/completions`). The one place an LLM client
gets constructed is `_build_llm()` in `app/graph.py`:

```python
llm = ChatOpenAI(
    model=LLM_MODEL,
    api_key=LLM_GATEWAY_API_KEY,       # llm-gateway doesn't care what this is
    base_url=LLM_GATEWAY_URL.removesuffix("/chat/completions"),
    timeout=LLM_REQUEST_TIMEOUT_SECONDS,
    temperature=0,
)
```

Because `llm-gateway` speaks the OpenAI chat-completions schema, LangChain's
`ChatOpenAI` (and its tool-calling / `bind_tools` support) work against it
unmodified - we get LangGraph + LangChain tool-calling for free without a
real provider SDK anywhere in this service.

## The shared MCP tool server

`app/mcp_server.py` exposes the same two tools (`search_knowledge_base`,
`crm_lookup`) as an **MCP server**, using the official `mcp` Python SDK, over
SSE transport, mounted at `/mcp` (full paths: `/mcp/sse` and
`/mcp/messages/`).

Both the LangGraph agent's tools (`app/graph.py`) and the MCP server
(`app/mcp_server.py`) are thin wrappers around the **same** implementation
functions in `app/tools_impl.py` - there is exactly one place that knows how
to call rag-service or look up a CRM record, wrapped twice for two different
callers.

> Note on the SDK version installed in this project (`mcp==2.0.0`): its
> ergonomic decorator-based server class is `mcp.server.mcpserver.MCPServer`.
> Some SDK versions ship the same shape of API under the older name
> `FastMCP` - the `@mcp.tool()` decorator / `.sse_app()` surface is what
> matters and is stable across both names. This service was written and
> tested against the installed `MCPServer` API directly (see
> `app/mcp_server.py`).

**Why a shared MCP server matters:** in many production setups, a
text/SMS conversational agent and a voice agent are two separate AI
products, but they both need the same customer CRM context and the same product
knowledge base. Instead of each product re-implementing its own CRM client
and its own RAG integration, both attach to **one shared MCP tool server**.
The tool/knowledge-base layer is built and maintained once; every AI product
that needs "look up this customer" or "search the docs" just speaks MCP to
the same server. This project's `agent-service` plays that shared-server
role in miniature: exactly one implementation of `crm_lookup` and
`search_knowledge_base`, reachable both by this service's own LangGraph
agent (in-process) and by any other MCP client on the network (out-of-process,
over `/mcp`).

## API

### `POST /chat`

```json
{"session_id": "abc123", "message": "What plan is cust_001 on?", "customer_id": "cust_001"}
```

`customer_id` is optional (mirrors how a real chat widget already knows
who's logged in, rather than parsing an ID out of free text the way the
`crm_lookup` tool does) - when given, it feeds live customer-context
features from Feast into the urgency classifier alongside the message text.
See "Feature-store integration" below.

Response:

```json
{
  "response": "Jane Doe is on the Pro plan.",
  "tool_calls": ["crm_lookup"],
  "session_id": "abc123",
  "urgency": "normal",
  "urgency_model_version": "3"
}
```

`tool_calls` lists every tool invoked during this turn, in order - useful
for showing a reader (or a debugging engineer) the agent's actual reasoning
path through the graph.

`urgency` / `urgency_model_version` are the live MLflow Model Registry
integration (see `app/model_registry.py` and `../mlflow/README.md`) - every
message is classified by whichever version is currently tagged `champion`
for `support-urgency-classifier`, and an `urgent` classification injects a
one-off system hint (see `URGENCY_SYSTEM_HINT` in `app/main.py`) that
visibly changes the response's tone. `urgency_model_version` is `null` if
no model has loaded yet (MLflow unreachable, or `../mlflow/run_training.sh`
hasn't been run) - this degrades gracefully, never breaks `/chat`.

### Feature-store integration

The classifier combines message text with **live** customer-context
features (`engagement_score`, `days_since_last_contact`,
`total_conversations`, `open_tickets`) looked up from Feast's online store
via `app/feature_store_client.py` on every turn a `customer_id` is given -
see `../feature-store/README.md`'s "Live integration" section for the
concrete proof (same message, different customer, different urgency label).
No `customer_id`, an unreachable `feature-store`, or an unknown customer all
degrade to neutral default features rather than breaking `/chat`.

Conversation history is kept in an **in-memory dict keyed by `session_id`**
(see `_SESSIONS` in `app/main.py`). This is explicitly **not durable** - a
restart of this process loses every in-flight conversation. A real
deployment of this pattern would back this with Redis (fast, TTL'd session
state) or Mongo (durable transcript storage), the way a lot of production
session stores work; swapping in either would only touch how `_SESSIONS` is read and
written in `app/main.py`.

### `GET /health`

Liveness probe: `{"status": "ok", "service": "agent-service"}`.

### `GET /admin/model-status` and `POST /admin/reload-model`

The live MLflow integration's introspection/control surface — see
`app/model_registry.py`. `GET /admin/model-status` snapshots what's
currently loaded (model name, alias, loaded version, refresh interval)
without forcing a reload. `POST /admin/reload-model` forces an immediate
reload from MLflow's `champion` alias, bypassing the normal 60s
refresh-interval cache — use this right after flipping an alias in
`../mlflow/train_and_log.py` so a demo doesn't have to wait.

### `GET /metrics`

Prometheus text exposition format, including:

- `agent_service_chat_requests_total{status="ok"|"error"}`
- `agent_service_chat_latency_seconds` (histogram)
- `agent_service_tool_calls_total{tool_name="search_knowledge_base"|"crm_lookup"}`
- `agent_service_urgency_model_version_loaded` (gauge) — the concrete
  "did my MLflow promotion actually reach this service" signal.
- `agent_service_urgency_classifications_total{label="urgent"|"normal"|"unknown"}`

### Langfuse tracing

If `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` are all
set, every `/chat` turn is wrapped in a Langfuse span (see
`app/tracing.py`). If any are missing, tracing silently no-ops - no code
elsewhere has to care whether tracing is active.

## Online evaluation: sampled, asynchronous LLM-as-judge scoring

`app/online_eval.py` samples a fraction of real `/chat` traffic
(`ONLINE_EVAL_SAMPLE_RATE`, default `0.3`) and grades each sampled response
with an LLM judge — but not the same way `eval-service` does. There's no
predefined `success_criteria` here (an arbitrary live user message doesn't
come with one), and the judge call is scheduled via FastAPI's
`BackgroundTasks` so it runs strictly *after* the `/chat` response has
already been sent — a slow or failed judge call is invisible to whoever sent
the original request, by construction. See the root README's "How it all
works" §3 for how this, `eval-service`'s offline scenario judge, and
`llm-gateway`'s inline guardrails are three genuinely different things the
industry all calls "evaluating an LLM system."

The resulting score is attached directly to that request's own Langfuse
trace (`Langfuse.create_score(trace_id=..., name="online-quality", ...)`),
so it shows up as a score badge on the trace you'd already be looking at —
not as a disconnected report. It's also exposed as
`agent_service_online_eval_runs_total{status}` and
`agent_service_online_eval_score_percent` on `/metrics`.

To see it fire reliably in a demo (the default 30% sample rate means most
individual requests won't be sampled), fire a few `/chat` requests and check
the logs:

```bash
for i in 1 2 3 4 5; do
  curl -s http://localhost:8003/chat -H "Content-Type: application/json" \
    -d "{\"session_id\": \"eval-demo-$i\", \"message\": \"What plans do you offer?\"}" > /dev/null
done
docker compose logs agent-service --tail 50 | grep "online eval"
```

## Manually testing this service

### `/chat` via curl

```bash
curl -s http://localhost:8003/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "demo-1", "message": "What plan is cust_001 on?"}' | python -m json.tool
```

Try a knowledge-base question in the same session to see `tool_calls`
change:

```bash
curl -s http://localhost:8003/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "demo-1", "message": "How do I upgrade my plan?"}' | python -m json.tool
```

(Both examples assume `rag-service` and `llm-gateway` are actually running
and reachable, e.g. via the project's top-level `docker-compose.yml`.)

### `/mcp` via the MCP inspector

The MCP SDK ships an official interactive inspector. With this service
running (`uvicorn app.main:app --port 8003` or via Docker), poke at its
tools directly, bypassing the LangGraph agent entirely:

```bash
npx @modelcontextprotocol/inspector
```

Then, in the inspector UI, connect with transport **SSE** and URL:

```
http://localhost:8003/mcp/sse
```

You should see both `search_knowledge_base` and `crm_lookup` listed as
available tools, and can invoke them directly with arbitrary arguments (try
`crm_lookup` with `identifier` = `cust_001`, `+15559876543`, or
`jane.doe@example.com` - all three resolve to the same mocked record).

## Local development

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # includes requirements.txt + test deps

# run the test suite (no live llm-gateway/rag-service needed - see below)
pytest -v

# run the service locally
uvicorn app.main:app --reload --port 8003
```

## Tests

`tests/` covers:

- `test_crm_tool.py` - the mocked in-memory CRM (`app/tools_impl.py`):
  lookup by phone / email / customer_id, case-insensitivity, misses.
- `test_knowledge_base_tool.py` - the `search_knowledge_base` tool's HTTP
  call to rag-service, mocked with `unittest.mock`.
- `test_graph.py` - the core deliverable: the LangGraph routing/loop-guard
  logic, both as unit tests of `route_after_router` and as full graph runs
  (`router -> tool -> router -> respond`) with the LLM call replaced by a
  scripted fake (see `tests/conftest.py`'s `patch_build_llm` fixture). This
  includes a test that deliberately makes the model request a tool forever,
  and asserts the loop guard still terminates the turn within
  `MAX_ITERATIONS` router visits.
- `test_mcp_server.py` - the MCP server lists both tools and they delegate
  to the same shared implementations.
- `test_api.py` - the `/chat`, `/health`, `/metrics` HTTP contract, with
  `run_agent_turn` mocked so these stay pure HTTP-layer tests.

None of the tests require a live `llm-gateway` or `rag-service`: the LLM
call is mocked at the `app.graph._build_llm` seam this service itself
defines (a boundary that's stable regardless of which HTTP client the
`openai`/`langchain-openai` SDKs use internally), and the rag-service HTTP
call is mocked with `unittest.mock.patch`.

## Docker

```bash
docker build -t agent-service .
docker run --rm -p 8003:8003 agent-service
```

Multi-stage build (`python:3.11-slim`): a `builder` stage installs
dependencies into a venv, and the final `runtime` stage copies only that
venv plus `app/` and runs as a non-root `appuser` (uid 1000).

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_GATEWAY_URL` | `http://llm-gateway:8001/v1/chat/completions` | The only LLM entrypoint this service is allowed to call |
| `RAG_SERVICE_URL` | `http://rag-service:8002/query` | Backing knowledge base for `search_knowledge_base` |
| `LLM_MODEL` | `gpt-4o-mini` | Model name forwarded to llm-gateway |
| `LLM_GATEWAY_API_KEY` | `not-needed-llm-gateway-handles-auth` | Sent as the OpenAI-client API key; llm-gateway does its own auth |
| `LLM_REQUEST_TIMEOUT_SECONDS` | `30` | Per-call timeout to llm-gateway |
| `MAX_ITERATIONS` | `5` | Loop-guard ceiling on router<->tool round-trips per turn |
| `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | unset | Optional tracing; no-ops if any are missing |
| `ONLINE_EVAL_SAMPLE_RATE` | `0.3` | Fraction of `/chat` requests sampled for asynchronous LLM-as-judge scoring (see above). Set to `0` to disable. Real production setups typically sample much lower (1-20%); this defaults higher purely so the pattern is easy to observe locally |
| `JUDGE_MODEL` | `gpt-4o-mini` | Model name forwarded to llm-gateway for the online-eval judge call |
| `SERVICE_PORT` | `8003` | Informational; the container's `CMD` binds 8003 directly |
