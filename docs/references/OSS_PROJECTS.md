# The AI/ML/LLM Ops landscape — best open-source projects by category

A map of the real ecosystem this whole project sits inside, organized by the job each tool does. Where a real production AI stack already uses one of these, it's noted — most of this list is what such an org *doesn't* currently use, for context on how big the surrounding landscape is.

## Agent orchestration / LLM app frameworks
- **[LangChain](https://github.com/langchain-ai/langchain)** — the most widely adopted LLM app framework (chains, tool-calling agents, retrievers).
- **[LangGraph](https://github.com/langchain-ai/langgraph)** — graph/state-machine based agent orchestration from the LangChain team; explicit cycles, persistence, human-in-the-loop interrupts. A newer alternative many production systems haven't adopted yet (see `02-concepts/02-llmops-mlops-tooling.md` §2) — used in this project's `agent-service`.
- **[LlamaIndex](https://github.com/run-llama/llama_index)** — the other major framework, historically RAG/data-indexing-first rather than agent-first; strong for document-heavy pipelines.
- **[Haystack](https://github.com/deepset-ai/haystack)** — production-oriented RAG/search pipeline framework, popular in enterprise search use cases.
- **[CrewAI](https://github.com/crewAIInc/crewAI)** — role-based multi-agent orchestration (agents as "crew members" with defined roles), simpler mental model than LangGraph for pure multi-agent handoff.
- **[Microsoft AutoGen / AG2](https://github.com/ag2ai/ag2)** — conversational multi-agent framework from Microsoft Research, agents "talk" to each other in a structured loop.
- **[Microsoft Semantic Kernel](https://github.com/microsoft/semantic-kernel)** — .NET/Python/Java SDK for orchestrating LLM calls + plugins, popular in enterprise .NET shops.

## Low-code / visual agent builders
- **[Langflow](https://github.com/langflow-ai/langflow)** — drag-and-drop LangChain/agent pipeline builder. See the tooling doc for why a codebase this custom (multi-provider fallback, MCP, per-service auth) tends to outgrow visual builders fast.
- **[Flowise](https://github.com/FlowiseAI/Flowise)** — similar visual builder, Node.js-based.
- **[n8n](https://github.com/n8n-io/n8n)** — general workflow automation tool with strong AI-node support; often used to glue LLM calls into existing business workflows rather than build agents from scratch.

## LLM observability / tracing
- **[Langfuse](https://github.com/langfuse/langfuse)** — self-hosted or cloud LLM observability (traces, evals, prompt management, cost tracking). A conversational-AI org might self-host this for its production backend and eval pipelines; this project's `services/langfuse/` mirrors that exact deployment shape.
- **LangSmith** (langchain-ai, source-available not fully OSS) — the LangChain team's own hosted tracing/eval platform. It's common for one service to end up on a different tracing vendor than the rest of an org's stack, on purpose or not.
- **[Arize Phoenix](https://github.com/Arize-ai/phoenix)** — open-source LLM observability + evals, strong on embedding/drift visualization.
- **[OpenLLMetry / Traceloop](https://github.com/traceloop/openllmetry)** — OpenTelemetry-native LLM instrumentation, vendor-neutral (exports to any OTel backend). Closest match to a service that instruments itself directly with OTel, generalized into a library.
- **[Opik](https://github.com/comet-ml/opik)** (Comet) — newer OSS LLM eval/observability platform.
- **[Helicone](https://github.com/Helicone/helicone)** — lightweight LLM observability proxy, easy to bolt onto an existing OpenAI-compatible setup (conceptually similar to what this project's `llm-gateway` could grow into).

## Evaluation frameworks
- **[Ragas](https://github.com/explodinggradients/ragas)** — RAG-specific eval metrics (faithfulness/groundedness, answer relevance, context precision/recall) — the OSS equivalent of what `langsmithdatasets`' hand-rolled graders do.
- **[DeepEval](https://github.com/confident-ai/deepeval)** — pytest-style LLM eval framework, popular for wiring evals into a normal test suite / CI.
- **[promptfoo](https://github.com/promptfoo/promptfoo)** — config-driven prompt/model eval and red-teaming CLI, good for eval-gating a CI pipeline (exactly the gap flagged in `ci-cd/README.md`).
- **[OpenAI Evals](https://github.com/openai/evals)** — the original open framework for defining/running model evals.
- **[TruLens](https://github.com/truera/trulens)** — eval + tracing focused on "trust" metrics (groundedness, relevance, safety).

## MLOps: experiment tracking, model registry, pipelines
- **[MLflow](https://github.com/mlflow/mlflow)** — experiment tracking + model registry + deployment. Not used anywhere in a lot of orgs' AI stacks today; this project's `services/mlflow/` is the concrete "here's what you're missing" exercise for baseline/rollback.
- **[Weights & Biases](https://github.com/wandb/wandb)** — the dominant commercial/hosted alternative to MLflow's tracking piece (client SDK is OSS, backend is hosted/enterprise).
- **[DVC](https://github.com/iterative/dvc)** — git-like version control for datasets/models, often paired with MLflow.
- **[ClearML](https://github.com/allegroai/clearml)** — full MLOps suite (experiment tracking, orchestration, model serving) in one OSS package.
- **[Kubeflow](https://github.com/kubeflow/kubeflow)** — Kubernetes-native ML pipelines, training orchestration, and serving. Not usable without first adopting Kubernetes for an org that runs entirely on something like ECS instead — see the tooling doc §6 for why that's a real prerequisite, not a detail.
- **[Metaflow](https://github.com/Netflix/metaflow)** (Netflix) — human-friendly Python framework for data science pipelines, scales from a laptop to the cloud without requiring Kubernetes first — often the practical alternative to Kubeflow for teams not already on k8s.
- **[ZenML](https://github.com/zenml-io/zenml)** — pipeline framework that stays infrastructure-agnostic (can target Kubeflow, SageMaker, local, etc. via pluggable "stacks").
- **[Flyte](https://github.com/flyteorg/flyte)** — Kubernetes-native workflow orchestrator, similar niche to Kubeflow Pipelines but with a stronger typed-data-passing model.

## Feature stores
- **[Feast](https://github.com/feast-dev/feast)** — the most widely adopted OSS feature store; used in this project's `services/feature-store/` demo, entirely file/SQLite-based for local use, scales to Redis/BigQuery/Snowflake in production.
- **[Hopsworks](https://github.com/logicalclocks/hopsworks)** — full feature-store platform with a built-in feature registry UI and heavier infra footprint than Feast.
- **Tecton** — commercial, built by Feast's original creators; not OSS but frequently mentioned alongside it.

## Vector databases
- **[Weaviate](https://github.com/weaviate/weaviate)** — a conversational-AI service's task definitions might carry an orphaned `WEAVIATE_DB_URI`-style secret, suggesting this (or something like it) once backed a shared MCP server's knowledge-base search — the kind of dead-but-still-present credential worth flagging in an infra audit.
- **[Milvus](https://github.com/milvus-io/milvus)** / Zilliz Cloud — high-scale vector search, popular for large-corpus RAG.
- **[Qdrant](https://github.com/qdrant/qdrant)** — Rust-based, strong filtering/payload support alongside vector search.
- **[Chroma](https://github.com/chroma-core/chroma)** — the simplest to run embedded/locally; used by this project's `rag-service`.
- **[pgvector](https://github.com/pgvector/pgvector)** — vector search as a Postgres extension; a common example of scaffolding (env vars, a pgvector Docker image) that gets provisioned in a context layer but never actually wired up to a model or table.

## Model serving / inference infrastructure
- **[vLLM](https://github.com/vllm-project/vllm)** — high-throughput LLM inference server (continuous batching, PagedAttention); the standard choice for self-hosting an open-weight model at scale.
- **[Hugging Face TGI](https://github.com/huggingface/text-generation-inference)** — similar niche to vLLM, tightly integrated with the HF ecosystem.
- **[Triton Inference Server](https://github.com/triton-inference-server/server)** (NVIDIA) — general-purpose model server, not LLM-specific, used heavily for classical ML/CV serving too.
- **[BentoML](https://github.com/bentoml/BentoML)** — packages any Python model (classical ML or LLM) into a deployable service with a consistent API.
- **[Ray Serve](https://github.com/ray-project/ray)** — model serving built on Ray's distributed compute framework, good fit if you already need distributed training/data processing.
- **[LiteLLM](https://github.com/BerriAI/litellm)** — unified proxy/SDK across 100+ LLM providers with a consistent OpenAI-compatible interface. A production conversational-AI backend might already use this; this project's `llm-gateway` is a hand-built version of the same idea, small enough to read end-to-end in one sitting.
- **[Ollama](https://github.com/ollama/ollama)** — the easiest way to run quantized open models locally (GGUF format); often used by an internal code-review service running models fully offline, and by this project's `llm-gateway`.
- **[llama.cpp](https://github.com/ggml-org/llama.cpp)** — the underlying inference engine Ollama wraps; worth knowing about directly if you ever need fine-grained control over quantization format/hardware acceleration.

## Guardrails / safety
- **[Guardrails AI](https://github.com/guardrails-ai/guardrails)** — structured output validation + safety checks (PII, toxicity, jailbreak) as a Python decorator/wrapper.
- **[NVIDIA NeMo Guardrails](https://github.com/NVIDIA/NeMo-Guardrails)** — programmable conversational rails (topic restriction, fact-checking hooks) for LLM apps.
- **[LLM Guard](https://github.com/protectai/llm-guard)** — input/output scanners (prompt injection, PII leakage, toxic content) designed to sit in front of/behind any LLM call.

## Data ingestion for RAG
- **[Unstructured](https://github.com/Unstructured-IO/unstructured)** — parses PDFs/HTML/Office docs into clean chunks for embedding pipelines; the kind of thing a knowledge-base ingestion pipeline could use if it needed to go beyond hand-authored markdown.

## Monitoring / observability (general infra, not LLM-specific)
- **[Prometheus](https://github.com/prometheus/prometheus)** + **[Grafana](https://github.com/grafana/grafana)** — the OSS standard for metrics + dashboards; used in this project's `services/observability/`, and the concrete gap-filler versus a logs-only observability setup that many orgs still run today.
- **[OpenTelemetry](https://github.com/open-telemetry/opentelemetry-python)** — vendor-neutral tracing/metrics instrumentation standard; often the one service in an org already using this directly, ahead of everything else.
- **[Netdata](https://github.com/netdata/netdata)** — real-time infra monitoring with built-in ML-based anomaly detection, one of the few genuinely OSS examples of AIOps-style anomaly detection rather than just dashboards.

## Cloud-managed equivalents (not OSS, included for comparison)
Since the concepts docs cover these in depth (`02-concepts/02-llmops-mlops-tooling.md` §7), just the one-line map here:
- **Amazon SageMaker** ≈ train/host/manage your own or fine-tuned models — the managed answer to "MLflow + a training pipeline + a serving layer" combined.
- **Amazon Bedrock** ≈ unified hosted-API access to multiple foundation models — the managed, AWS-native answer to what LiteLLM/this project's `llm-gateway` do yourself.
- **Google Vertex AI** / **Azure Machine Learning** — the GCP/Azure equivalents of the SageMaker niche.

## How to actually explore these further
Cloning and running any of these locally is a legitimate next step after this project — most have a `docker-compose.yml` or a 5-minute "quickstart" in their README. A good next move if a specific category above is interesting: pick one (e.g. try swapping this project's Chroma for Qdrant, or its hand-rolled `llm-gateway` for a real LiteLLM proxy) and see what changes.
