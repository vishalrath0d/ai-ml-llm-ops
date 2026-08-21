# Foundational AI/ML Concepts

This document walks through the core AI/ML/LLM vocabulary a DevOps engineer needs to operate and reason about a production AI stack. Every "how a real system implements this" note below is drawn from patterns commonly seen across production conversational-AI, voice-AI, and LLM-eval systems — not from what a typical AI stack "should" look like in theory.

## 1. AI vs ML vs Generative AI vs Agentic AI

These four terms get used interchangeably in casual conversation, but they describe nested, increasingly specific things.

**Artificial Intelligence (AI)** is the umbrella term: any system that performs a task normally requiring human judgment — pattern recognition, decision-making, language understanding. A hand-written rules engine that routes support tickets by keyword is technically "AI" in the broadest historical sense, even though nothing is "learned."

**Machine Learning (ML)** is a subset of AI where the system's behavior is *learned from data* rather than hand-coded. Instead of writing `if subject contains "refund" then route to billing`, you feed a model thousands of labeled examples and it learns the mapping itself. Classic ML includes things like fraud-detection models, churn-prediction models, spam classifiers — trained on structured/tabular data, producing a narrow prediction (a number, a class label). It's common for an org's AI investment to be almost entirely about the next layer up (LLMs), not classic supervised ML, especially early on — no churn model or scoring model yet, just conversational AI.

**Generative AI** is a subset of ML where the model's output is itself unstructured content — text, code, images, audio — rather than a label or number. Large Language Models (LLMs) are the text-generation case. This is where most of a modern org's AI investment tends to sit: one service generates chat replies, another generates spoken conversation turns, another generates code-review comments.

**Agentic AI** is generative AI wired into a *loop* that can take actions in the world and react to what happens, rather than just emitting one text response to one prompt. An agent doesn't just answer "the customer's order status is X" — it can decide to call a CRM tool to look up the order, read the result, and decide the next step. The defining feature is autonomy over multiple steps, not just single-shot generation.

### The perception → reasoning → tool use → action → observation loop

Every agentic system, however it's built, follows the same loop shape. Here's how a real request flows through a production conversational-AI system, concretely:

```
1. PERCEPTION
   Inbound chat message + chat history arrive at the API.
   Chat history is loaded; if the conversation is long, it has already
   been LLM-summarized so the context stays small.

2. REASONING / PLANNING
   The message + history + system prompt go to the LLM (a primary
   provider, with fallback providers via LangChain's `.with_fallbacks()`).
   The LLM decides: "Can I answer directly, or do I need external info?"

3. TOOL USE
   If external info is needed, the LLM emits a tool-call (e.g. "search
   knowledge base for refund policy" or "look up this contact in CRM").
   LangChain's AgentExecutor intercepts this and routes it — via an MCP
   client — to an external MCP server, which actually holds the KB
   search / CRM logic.

4. ACTION
   The MCP server executes the real lookup (KB search, CRM query) and
   returns structured results to the agent loop.

5. OBSERVATION
   The tool result is appended back into the LLM's context. The LLM now
   has the retrieved facts and decides whether it has enough to answer,
   or needs another tool call (loop back to step 2).

6. Once the LLM has enough, it emits the final natural-language reply,
   which passes through a moderation check before going back to the
   customer.
```

This is the "ReAct-style" loop (Reason, Act, Observe) that underlies almost every agent framework, whether it's LangChain's AgentExecutor or LangGraph (more on the difference in the Multi-Agent section below). The loop can run 1 time (simple Q&A, no tools needed) or several times in a row (multi-hop: look up the contact, then look up their orders, then answer).

**Why this distinction matters operationally:** classic ML models are static once deployed — same input always gives the same output, and failure modes are usually data-drift related. Generative AI outputs vary run-to-run (non-determinism, covered in the next section) and can fail in more entertaining ways — hallucination, prompt injection via tool results. Agentic AI adds a third failure class on top: infinite tool-call loops, wrong tool selection, or an agent taking an *action* (not just saying something wrong, but calling the wrong tool with real side effects) — which is exactly why moderation checks and MCP-server boundaries matter as containment layers, not just "nice to have" plumbing.

## 2. LLM Fundamentals: Tokens, Context Windows, Temperature, Roles, Tool Calling

An LLM is, mechanically, a function that takes a sequence of tokens and predicts the probability distribution of the next token, repeated until it decides to stop. Everything else — chat interfaces, agents, RAG — is scaffolding built around that one operation.

**Tokens.** Text isn't fed to the model character-by-character or word-by-word; it's broken into sub-word chunks called tokens (roughly 4 characters of English per token on average, so "internationalization" might be 4-5 tokens, not 1). This matters practically because you're billed per token and the context window is measured in tokens, not characters or messages. A production chat exchange with a long customer history and a KB-search tool result injected back into context can easily consume several thousand tokens per turn before the model even generates a reply.

**Context window.** This is the maximum number of tokens (input + output combined) the model can "see" at once — e.g. 128K tokens for GPT-4o-class models. Anything older than that in a conversation simply falls out of view. This is precisely why a lot of production conversational-AI systems implement automatic LLM-based summarization of chat history once a conversation gets long: rather than truncating (losing information) or blowing the context window (increased cost, latency, and "lost in the middle" accuracy degradation), it periodically asks an LLM to compress the older history into a summary, then carries that summary forward instead of the raw transcript. This is a very common production pattern — context window management is an active engineering problem, not something you get for free.

**Temperature.** A parameter (typically 0–2) controlling how "confident vs. exploratory" the next-token sampling is. Temperature 0 makes the model nearly deterministic — always picks the highest-probability token (useful for classification-style tasks, JSON extraction, or LLM-as-judge scoring where you want reproducibility). Higher temperature (0.7-1.0) introduces randomness, useful for conversational variety so replies don't feel robotic and repetitive. This directly explains why production eval services commonly use LLM-as-judge scoring instead of exact-string-match testing — at any temperature above 0, the same input can legitimately produce different phrasing on every run, so testing "does the output exactly equal X" is the wrong tool; testing "does a judge model rate this response as correct" is the right one.

**System / user / assistant roles.** Modern LLM APIs (OpenAI, Anthropic, Gemini) all use the same three-role message format: `system` (instructions defining the model's behavior/persona/constraints — set once, not from user input), `user` (the actual human input), `assistant` (the model's own prior replies, included so it has memory of the conversation). A multi-turn chat isn't one API call — it's the *entire* running list of system+user+assistant messages resent every single time, which is another reason context-window and token-cost management (see above) matters: cost scales with the whole conversation, not just the newest message.

**Function/tool calling.** Instead of only returning text, the model can be given a list of available "tools" (function name, description, JSON schema of parameters) and, instead of an answer, respond with "call function X with these arguments." The calling application executes the real function and feeds the result back in as a new message, and the model continues. This is the mechanism underneath the entire agent loop from Section 1. A production conversational-AI agent might use LangChain's `create_tool_calling_agent` + `AgentExecutor` specifically to manage this call → execute → feed-back → continue cycle, with tools exposed via MCP rather than defined locally in the application.

**Multi-LLM fallback, concretely.** A production system might wrap its primary model call with LangChain's `.with_fallbacks()`: if the primary provider (say OpenAI) times out, rate-limits, or errors, the exact same request is retried against a different provider (Anthropic, then Gemini) with no special-casing in the business logic. This is possible specifically *because* all three providers converge on the same system/user/assistant/tool-call contract described above — that convergence is what makes provider-swapping at the infrastructure level a one-line `.with_fallbacks()` call instead of a rewrite. For a customer-facing chat product, this is a straightforward availability/SLA mitigation: a single provider's outage doesn't take down the chat feature.

## 3. Retrieval-Augmented Generation (RAG)

An LLM only knows what was in its training data (frozen at training time) plus whatever you put in its context window at request time. RAG is the pattern for getting live, private, or large-corpus information into that context window without retraining the model: **chunk → embed → store → retrieve → augment prompt → generate.**

1. **Chunk** — split source documents (KB articles, schema docs, transcripts) into smaller passages, because embedding and retrieval work better on focused chunks than whole documents.
2. **Embed** — run each chunk through an embedding model that converts it into a fixed-length numeric vector capturing its meaning (see Section 4).
3. **Store** — put those vectors in a vector database, indexed for fast similarity search.
4. **Retrieve** — at query time, embed the *user's question* the same way, and find the stored chunks whose vectors are closest to it.
5. **Augment prompt** — insert the retrieved chunks into the LLM's context ("here is relevant background, now answer the question").
6. **Generate** — the LLM produces an answer grounded in the retrieved text instead of relying solely on its training-time knowledge.

That's "naive RAG" — embed everything, cosine-search everything, stuff the top-K results into the prompt. It's the version every RAG tutorial teaches, and it's a reasonable default when you have a large, relatively uniform corpus and no strong opinions about which documents matter most for which query types.

**Where naive RAG breaks down** is exactly what a real internal data-engineering team's Slack NL-to-SQL bot was built to avoid, and its design is worth studying because it deliberately rejects the naive approach:

- **Cost/latency:** embedding-searching the entire schema corpus on every query, then stuffing large chunks of retrieved text into every prompt, means every request pays full embedding-search latency *and* loses LLM prompt-cache hits (providers cache repeated prefixes of a prompt to cut cost/latency — if the retrieved context differs every time, that cache never hits).
- **Precision:** semantic similarity search over schema documentation is not the same as "the exact table this specific question needs." A cosine-similarity match can retrieve plausible-looking but wrong or incomplete schema context, especially for domain jargon that doesn't embed distinctively.

Its actual hybrid 4-layer retrieval instead does:
1. **Always-included "invariant safety core"** — a small set of schema docs/rules that must be in every prompt regardless of the query (e.g. core safety and scoping rules) — no retrieval decision needed, no embedding cost, and it's the same text every time so it's a guaranteed prompt-cache hit.
2. **~80 curated keyword-trigger patterns** — hand-authored rules like "if the query mentions X, include table doc Y" — deterministic, zero embedding cost, catches the common cases precisely.
3. **Semantic search via ChromaDB + sentence-transformers (`all-MiniLM-L6-v2`)** — only invoked for whatever the keyword layer *doesn't* catch, i.e. semantic search is the fallback, not the primary mechanism.
4. **Structural auto-include rules** for complex multi-domain requests that need several schema areas at once, applied deterministically based on request shape.

The result: most queries never touch the vector database at all, prompt content is highly repetitive across requests (maximizing provider-side prompt-cache hits, which materially cuts cost and latency), and the docs that do get included are the docs a human decided are relevant for that trigger — not whatever an embedding model judged "similar enough."

**The general lesson:** naive RAG is a fine starting point, but "always embed and cosine-search" is a design decision with real cost/latency/precision consequences, not a law of nature. If you know your domain's query patterns (SQL questions about a fixed, known schema, for example), deterministic/keyword retrieval layered with semantic search as a fallback is often cheaper, faster, and more precise than pure vector search. RAG is a spectrum from "no retrieval logic, just embed everything" to "mostly rule-based, vector search as backstop" — pick a point on that spectrum based on how well-understood your query space is.

It's also worth noting explicitly: a high-traffic conversational-AI product doesn't always implement RAG locally at all — no vector DB client in its own code. Its knowledge-base search can be delegated entirely to an external MCP server. From the agent's point of view, "search the knowledge base" is just another tool call (Section 2) — the RAG pipeline behind that tool is someone else's implementation detail. This is a legitimate architecture choice: it means multiple AI products (a chat agent, a voice agent) can share one RAG implementation instead of each rebuilding chunk/embed/store/retrieve logic independently.

## 4. Vector Databases and Embeddings

An **embedding** is a fixed-length list of floating-point numbers (e.g. 384 or 1536 dimensions) produced by a model that's been trained so that pieces of text with similar *meaning* end up as vectors that are numerically close together, and dissimilar text ends up far apart — even if the actual words used are completely different. "The customer wants a refund" and "client is requesting their money back" would embed close together despite sharing almost no vocabulary; "the customer wants a refund" and "the weather is nice today" would embed far apart.

**Similarity search** at query time means: embed the query, then find which stored vectors are "closest" to it. The standard distance metric is **cosine similarity** — it measures the angle between two vectors rather than their absolute distance, which matters because it makes the comparison insensitive to vector magnitude/length differences and focuses purely on direction (i.e., meaning). A cosine similarity of 1.0 means identical direction (near-identical meaning); 0 means unrelated; -1 means opposite.

Doing this by brute force — comparing the query vector against every single stored vector one at a time — is O(n) and gets slow past a few tens of thousands of vectors. Production vector databases instead use **ANN (Approximate Nearest Neighbor) indexes** — data structures like HNSW (Hierarchical Navigable Small World graphs) that organize vectors so a search can skip most of the space and still find a "close enough" (approximate, not guaranteed-exact) set of nearest matches in roughly logarithmic time instead of linear. The tradeoff is a small chance of missing the true single-best match in exchange for orders-of-magnitude faster search at scale — a reasonable tradeoff for RAG, where "one of the top handful of relevant chunks" is what you need, not a mathematically perfect ranking.

**A vector-database footprint is often mostly aspirational, not operational** — worth understanding precisely because the gap between "provisioned" and "used" is itself a useful thing to notice as a DevOps engineer auditing a stack. It's a common enough pattern to be worth naming explicitly:

- A conversational-AI service might have an orphaned vector-DB secret sitting in its configuration — evidence that vector-based RAG was planned or trialed at some point — but its actual KB search ends up delegated entirely to an external MCP server, with no active vector-DB client in that repo's code today.
- A feature-serving layer might have Postgres+pgvector scaffolding — environment variables, a pgvector Docker image — that's never actually wired into any model or table. This is dead RAG infrastructure: provisioned, never connected to a code path.
- An internal data-engineering bot might have an *active* vector-DB instance, holding embeddings of schema documentation via a small, fast, locally-run embedding model (`sentence-transformers`, `all-MiniLM-L6-v2` — no external embedding API call needed), used as the semantic-search fallback layer described in Section 3.
- An eval-tooling service might also use a vector DB actively, as part of its eval tooling.

The practical takeaway: don't assume a `WEAVIATE_URL` env var or a pgvector Docker image in a docker-compose file means vector search is live. It's common for half or more of the vector-database traces in a real org's docker-compose files to be dead scaffolding — only the services that actually implement active retrieval query a vector store at runtime. If you're auditing infra cost or attack surface, this is exactly the kind of thing worth flagging — a running vector-DB instance that nothing reads from is both a cost and a security liability with zero product benefit.

## 5. Feature Stores

**TL;DR: a feature store is a database + API for ML input variables ("features") that guarantees the value a model was trained on and the value it sees in production are computed identically.** It sits between raw data (warehouses, event streams, application databases) and the models that consume it, acting as a shared, versioned catalog of "customer engagement score," "days since last purchase," etc. — computed once, served consistently to both offline training jobs and live online inference.

A **feature store** is infrastructure that solves two specific ML-engineering problems, both of which only exist once an org has multiple ML models in production:

**1. Train/serve skew.** A model is trained offline using features computed one way (e.g. a batch SQL job computing "average messages per week over the last 90 days" from a data warehouse), but at inference time in production, the same feature needs to be computed again, live, usually in a completely different code path (an application service, not a batch job) — and if that live computation doesn't match the offline computation exactly (different time windows, different null-handling, different join logic), the model sees different-looking input in production than it learned from in training. This mismatch silently degrades model quality in ways that are hard to detect. A feature store solves this by centralizing the feature *definition* once, and serving it consistently to both the offline training pipeline and the online inference path.

**2. Feature reuse across models/teams.** Without a feature store, every team computing "customer engagement score" or "days since last purchase" writes their own version, duplicating pipeline logic and often producing subtly different numbers for the "same" feature across models. A feature store lets one team publish a feature once and have every other model/team consume the identical, versioned definition.

**A lot of orgs have no feature store, and — more fundamentally — don't yet have the situation that would make one worth building.** Feature stores earn their cost when an org has multiple ML models in production consuming overlapping features. It's common for an org's AI investment to be almost entirely LLM-based (chat, voice, code review, eval) rather than classic feature-engineered ML models early on, so there's no train/serve skew problem to solve yet — no offline training pipeline computing features at all.

**This is already built and runnable in this project**, not just described: [`../../services/feature-store/`](../../services/feature-store/) is a working [Feast](https://github.com/feast-dev/feast) setup — a toy `customer_id` entity, a `customer_engagement_features` feature view, fake historical data for 20 customers, and a `demo.py` you run yourself (`python demo.py`) that walks through `feast apply` → an offline point-in-time-correct training retrieval → materializing to an online store → an online serving-time lookup — with the values printed side by side so you can see train/serve consistency (or a deliberately-introduced skew) directly, instead of trusting this paragraph.

**Where this would concretely start to matter:** a production feature-serving layer might already compute conversation-level signals for a Next-Best-Action (NBA) engine — things like conversation graph structure and some notion of engagement or intent strength derived from conversation history. If the org later builds a genuinely separate ML model — say, a churn-prediction model for account health, trained offline on historical account data — and that model wants to consume a "customer engagement score" as an input feature, the naive path is: the churn model team re-derives their own engagement score from raw data, independently of whatever the feature-serving layer already computes for NBA. Now there are two different, subtly inconsistent definitions of "customer engagement" living in two codebases, and no way to guarantee the number the churn model trained on matches the number available at serving time. A feature store would let that engagement signal be defined once, versioned, and consumed identically by both the NBA engine and the churn model — but building one today, before a second consuming model exists, would be solving a problem the org doesn't have yet. This is worth flagging now specifically because it's the kind of infrastructure that's cheap to plan for early and expensive to retrofit once three or four models each have their own bespoke feature pipeline.

## 6. Model Quantization

LLM weights are, at heart, just an enormous number of floating-point numbers — a 7-billion-parameter model has 7 billion individual weight values. The precision used to store each of those numbers directly determines two things: how much memory/disk the model needs, and (on many hardware setups) how fast inference runs.

**The precision ladder**, from most to least precise:
- **FP32** (32-bit float) — full precision, rarely used for inference; mostly a training-time format.
- **FP16 / BF16** (16-bit float) — half the memory of FP32, the common default for GPU inference of full-size hosted models.
- **INT8** (8-bit integer) — a quarter the memory of FP32, with a small, usually acceptable accuracy loss.
- **INT4 / GGUF-packed formats** — roughly an eighth the memory of FP32, allowing multi-billion-parameter models to run on a single consumer GPU or even CPU-only, at the cost of more noticeable (though often still tolerable) accuracy degradation.

**Why it matters operationally:** a 7B-parameter model at FP16 needs roughly 14GB of memory just to hold the weights (2 bytes × 7B); the same model quantized to INT4 needs roughly 3.5-4GB. That's the difference between "needs a dedicated GPU with 16GB+ VRAM" and "runs comfortably on a laptop or a modest on-prem server." Quantization is precisely what makes **local/self-hosted LLM deployment** practical — running a model without paying per-token to a hosted API and without sending data outside your network.

**This connects directly to internal tooling like a code-review service** — it's common for exactly one fully local/offline LLM deployment to exist in an org, while every other AI product calls hosted OpenAI/Anthropic/Gemini APIs over the network. A code-review service might instead run models (llama3.1, deepseek-coder) via **Ollama**, reachable only from inside the corporate network. Ollama's default model distribution format is **GGUF**, a quantized weight-packing format — when you `ollama pull llama3.1`, you are, by default, pulling a quantized (commonly 4-bit) version of that model, not the original full-precision release. This is *why* it's feasible to run a capable code-reviewing LLM on internal infrastructure without provisioning a serious GPU cluster: quantization is the specific technical mechanism that makes "self-host an LLM for internal-only use" a reasonable engineering decision rather than a research-grade GPU-cluster project.

**The tradeoff to know about:** quantization is not free. Going from FP16 to INT4 typically costs a few percentage points of benchmark accuracy — usually acceptable for a task like "flag likely bugs and style issues in this diff" where the reviewer is a human making the final call, but potentially more consequential for a task requiring precise numeric reasoning or exact factual recall. This is also *why* it's common for nothing else in an org to run locally: customer-facing conversational products tend to need hosted frontier-model quality (full-precision GPT-4o/Claude/Gemini) more than the cost/data-locality benefits of a locally quantized model would justify. An internal code-review use case sits at a different point on that quality-vs-cost-vs-data-locality tradeoff curve, which is exactly why it's the outlier.

## 7. Multi-Agent Systems and Orchestration Patterns

A **multi-agent system** splits work across more than one LLM-driven "agent," each with a narrower role, instead of asking a single agent to do everything. The coordination problem — deciding which agent handles what, and how control passes between them — is called **orchestration**.

**Coordinator/sub-agent handoff, as commonly implemented in production conversational-AI systems:** rather than one monolithic agent with every possible tool and instruction bolted on, the request-handling entry point acts as a coordinator that determines which specialized agent/prompt configuration should handle a given conversation (e.g. routing between different behaviors or tool-sets depending on conversation context), and that sub-agent then runs its own tool-calling loop (Section 1/2) to completion. This is a lightweight orchestration pattern — a single dispatch decision followed by one agent execution — as opposed to the more general (and more complex) pattern of agents dynamically handing off to each other mid-task.

**Tool-calling agents (AgentExecutor) vs. graph-based agents (LangGraph):** it's worth being precise about the difference, since a lot of production systems have deliberately (or by default) stayed with the simpler one.

LangChain's classic `AgentExecutor` + `create_tool_calling_agent` — a common choice for production conversational-AI agents — implements the ReAct loop from Section 1 as an implicit, code-level loop: call the LLM, check if it wants a tool, execute the tool, feed the result back, repeat until the LLM stops requesting tools. Control flow is a `while` loop hidden inside the library; there's no separate representation of "what state is this agent in."

**LangGraph** (a newer alternative many production systems haven't adopted yet) is a different abstraction: you define the agent's control flow as an explicit **state machine/graph** — named nodes (each a step, e.g. "retrieve," "generate," "check-safety") and edges (transitions between them, including conditional branches and, notably, **cycles** — a node can route back to an earlier node, not just linearly forward). This buys you three things AgentExecutor doesn't give you directly:

1. **Explicit state machines** — the control flow is a data structure you can inspect, log, and unit-test node-by-node, rather than an implicit loop buried in a library call.
2. **Cycles as a first-class concept** — useful for patterns like "generate, critique, regenerate" loops or multi-agent back-and-forth, which are awkward to express as a single AgentExecutor loop.
3. **Human-in-the-loop interrupts** — LangGraph supports pausing execution at a specific node and waiting for a human to approve/edit state before resuming — useful for anything where you want a person to sign off before an agent takes a consequential action.

**Why this gap is worth knowing about:** an AgentExecutor pattern is genuinely sufficient for a lot of current-shape problems — bounded tool-calling within a single conversational turn, no need to pause mid-reasoning for human approval, no need for complex cyclical multi-agent back-and-forth. But if an org ever wants an agent that, say, drafts a CRM update and pauses for a human to approve before writing it (a natural extension of a feature-serving layer's NBA recommendations, or a more consequential version of an internal code-review tool), that's exactly the human-in-the-loop-interrupt capability AgentExecutor doesn't have and LangGraph is purpose-built for. Right now that's often a non-issue because no agent takes an irreversible action without additional application-level guardrails already in place — but it's the first thing to reach for if that requirement shows up.

## 8. Model Context Protocol (MCP)

**TL;DR: MCP is an open, standardized protocol (originally from Anthropic, now widely adopted across the industry) for how an LLM application discovers and calls external tools/data sources.** Think of it as "USB-C for AI tools" — one standard plug instead of a different bespoke integration for every (agent framework) × (tool) pair. An **MCP server** exposes tools/resources over this standard interface; any **MCP client** (an agent, an IDE, a chat app) that speaks MCP can discover and call them without custom glue code per client.

Before MCP, if you wanted an LLM agent to call an external tool — search a knowledge base, query a CRM, run a SQL query — you hand-rolled it: define the function schema in your framework's format, write the glue code to actually execute it, and repeat that work in every single service that needed the same tool. If three different AI products all need "search the knowledge base," naive architecture means three different hand-written KB-search integrations, each potentially drifting in behavior, each needing its own maintenance.

**MCP (Model Context Protocol)** standardizes this: it defines a common protocol for how an LLM-calling application (an "MCP client") discovers what tools/resources an external server exposes, and how it invokes them — regardless of which LLM provider or agent framework the client is using. An "MCP server" exposes a set of tools once, over a standard interface; any MCP-compatible client can discover and call them without custom integration code per client.

**This is a clean real-world illustration of exactly this problem being solved, seen across a lot of production systems:**

- A standalone, shared MCP server exposing knowledge-base search and CRM tools might be consumed by both a chat agent (via `langchain-mcp-adapters`, the MCP client library for LangChain) and a voice-AI backend (which attaches an MCP toolset per call to the same server). Two different products, on two different tech stacks, both get KB search and CRM tools without either one owning or reimplementing that logic. If the KB search implementation improves, both products benefit simultaneously with no code change on their side.
- A data-engineering team's internal Slack bot might take the inverse role: it **is itself an MCP server** (built with FastMCP), rather than only being an MCP client. Its NL-to-SQL capability over a data warehouse is exposed as a tool other MCP clients could in principle call, the same way a chat agent calls into a shared MCP server.

> **A DevOps lesson worth knowing on its own:** a shared MCP server's own docs might only track one confirmed consumer team and discuss "other clients" as a future risk — while in reality a second product already calls it today. A shared dependency can be real and still not be fully tracked by its own owning team.

**This is already built and runnable in this project too**: [`../../services/agent-service/`](../../services/agent-service/) plays *both* roles at once, the same way a service playing both MCP roles at once commonly does — it's an MCP **client** (its LangGraph agent calls a `search_knowledge_base` tool and a mocked `crm_lookup` tool) and it's simultaneously an MCP **server** (those same two tools are exposed over SSE at `/mcp` for any other MCP client to discover and call). Point `npx @modelcontextprotocol/inspector` at `http://localhost:8003/mcp` to see this from the client's side — you'll see the exact tool schemas an LLM would be given, which is the concrete thing MCP standardizes.

**Why this is worth understanding as a DevOps concern, not just an AI-engineering one:** MCP servers are network services with their own availability, auth, and blast-radius characteristics, same as any other internal API — a shared MCP server going down doesn't just break one product, it degrades KB search and CRM tools across every product that depends on it simultaneously, because they share that single dependency. That's the same shared-dependency tradeoff as any shared internal service (a shared auth service, a shared database) — centralizing tool logic in one MCP server buys consistency and avoids duplicated integration work, at the cost of a single point of failure that now sits in the critical path of multiple customer-facing products. Treat an MCP server in an org's architecture the way you'd treat any other shared internal API dependency: it needs its own uptime monitoring, rate limiting, and incident-response ownership, because its blast radius spans every client it serves.

## 9. Compute Hardware: CPU, GPU, TPU, NPU, and ASICs

All of these are processors. What differs between them is **how much they parallelize** and **how specialized they are** for the one operation neural networks are mostly built from: matrix multiplication.

- **CPU** — a handful of powerful, general-purpose cores (4-16 typically), optimized for sequential logic, branching, and low-latency single-task execution. Good at "do one complex thing fast," bad at "do the same simple operation a million times simultaneously" — which is exactly the shape of a neural network's forward pass.
- **GPU** — thousands of small, simple cores built to apply the same operation across many data points at once. Originally built for rendering pixels (the same parallel shape as matrix math), which is why GPUs became the default deep-learning accelerator once people repurposed them for it — a matrix multiply that takes a CPU seconds takes a GPU milliseconds. NVIDIA's CUDA ecosystem dominates this space today.
- **TPU** — Google's custom **ASIC** (see below), hardwired for exactly one thing: neural-network tensor math, nothing else. More specialized than a GPU (which still handles general graphics/compute), so it's faster and more power-efficient *per tensor operation* — but it's only available by renting time on Google Cloud, not a chip you buy and install.
- **NPU** — a small, low-power accelerator built into consumer chips (Apple's Neural Engine, Qualcomm's Snapdragon NPU, newer Intel laptop chips), purpose-built for running — usually small, quantized (see Section 6) — models **on-device**. Optimized for power efficiency over raw throughput, since it shares a battery-powered chip with everything else the device is doing.
- **ASIC (Application-Specific Integrated Circuit)** — the umbrella category TPUs belong to: a chip hardwired for one specific job rather than programmable for many. Extreme efficiency for that one job, zero flexibility for anything else — the opposite tradeoff from a CPU.
- **FPGA (Field-Programmable Gate Array)** — reconfigurable hardware, a middle ground between a general-purpose CPU and a hardwired ASIC. Rare in mainstream ML serving; shows up more in specialized/embedded inference.

**The one-line mental model**: CPU = flexible, low parallelism. GPU = high parallelism, general-purpose math. TPU/ASIC = maximum specialization for one math pattern, zero flexibility. NPU = a GPU's low-power cousin, built for your phone or laptop, not a data center.

### Where and how a workload should actually run

This is a decision made **per workload stage**, not once for an entire system — training, fine-tuning, and serving have different shapes, and a small model has a completely different answer than a large one:

| Stage | Typical hardware | Why |
|---|---|---|
| Training a large model from scratch | GPU/TPU clusters (cloud) | Needs massive parallel throughput for days/weeks — essentially nobody self-hosts at this scale |
| Fine-tuning a mid-size model | A handful of cloud GPUs, rented for hours | Bursty and short-lived, which matches cloud's pay-per-use pricing shape (see `04-finops-and-production-judgment.md` §1) |
| Serving a large model at high/steady traffic | Dedicated GPU instances, kept warm | Latency-sensitive; spinning up a GPU per request is too slow |
| Serving a small model (≤a few billion params) at low/spiky traffic | Plain CPU is often enough | Parallelism matters less at small scale; GPU cost/complexity isn't justified by the volume |
| On-device inference (offline mode, a phone app) | NPU | Battery-powered, no network round-trip, a small quantized model |
| Classification-shaped subtasks (routing, urgency-scoring, intent detection) | CPU, and often not even a neural net | These almost never need GPU-scale compute — see below |

**This project makes the CPU-vs-GPU tradeoff concrete rather than theoretical, including its failure mode.** [`../../services/llm-gateway`](../../services/llm-gateway) runs Ollama's `qwen2.5:0.5b` **on plain CPU**, which works specifically because the model is tiny. A real incident during this project's own build (documented in `04-finops-and-production-judgment.md` §9) showed the direct downside live: under host CPU contention from unrelated containers, that same CPU-only model took over two minutes to generate a response that normally takes seconds — CPU inference has no dedicated parallel hardware to fall back on when the CPU is shared. A 70-billion-parameter model would simply not run usably on this setup at all; that's the point where renting a cloud GPU stops being optional and becomes the only option. Separately, [`../../services/mlflow`](../../services/mlflow)'s urgency classifier is a small `LogisticRegression` — not an LLM, and not running on anything but CPU — because classification-shaped problems almost never need GPU-scale compute in the first place. Routing that kind of subtask to a tiny CPU-bound model instead of a GPU-bound LLM call is the same "cheap compute for cheap tasks" principle as `04-finops-and-production-judgment.md` §3's cost-aware-routing argument, just viewed from the hardware side instead of the dollar side.

## Try it yourself

Reading about tokens, RAG, and tool-calling is a poor substitute for running them. This project builds a small local version of the patterns described above so you can see them work (and break) directly:

- A minimal **RAG service** — chunk a small set of documents, embed them, store them in a local vector database, and run retrieval + generation end to end, so you can see chunk boundaries, similarity scores, and prompt augmentation with your own eyes instead of trusting a diagram.
- An **MCP-exposing agent service** — a small tool server speaking MCP, plus a client agent that discovers and calls its tools, mirroring the shared-MCP-server relationship described in Section 8 at a scale you can fully read in one sitting.
- Room to experiment with the concepts above directly: swap temperature settings and observe output variance, deliberately overflow a context window and watch behavior degrade, quantize a small local model and compare its output against the full-precision version.

Treat this project as the place to falsify or confirm what this document claims — if something here doesn't match what you observe running it locally, the local behavior is the ground truth.
