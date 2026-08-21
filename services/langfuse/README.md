# Langfuse — local equivalent of a real prod tracing stack

## Why this exists / what it mirrors

Plenty of production LLM systems run self-hosted Langfuse behind a
subdomain like `traces.example-aiops.internal`, provisioned via
Terraform on ECS. This folder is **not** introducing a new tool — it's a
local docker-compose stand-in for that exact same architecture, so you can
develop and demo LLM tracing without touching prod infra:

| Component        | Prod (Terraform/ECS)              | Local (this folder)         |
|-------------------|------------------------------------|------------------------------|
| Web + API         | ECS service, `langfuse/langfuse`   | `langfuse-web` container     |
| Background worker | ECS service, `langfuse/langfuse-worker` | `langfuse-worker` container |
| Relational DB     | RDS Postgres                       | `langfuse-db` (postgres:16)   |
| Analytics store   | ClickHouse (v3 requirement)        | `langfuse-clickhouse`        |
| Queue/cache       | ElastiCache Redis                  | `langfuse-redis`             |
| Blob storage      | S3                                 | `langfuse-minio` (S3-compatible) |

Every other service in this project (`llm-gateway`, `rag-service`,
`agent-service`, `eval-service`) already reads `LANGFUSE_HOST`,
`LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` from its environment and
no-ops gracefully if Langfuse isn't reachable — so this stack is purely
additive. Nothing breaks if you don't run it; you just won't see traces.

## Files

- `compose.fragment.yml` — the full stack: `langfuse-web`, `langfuse-worker`,
  `langfuse-db`, `langfuse-clickhouse`, `langfuse-redis`, `langfuse-minio`,
  plus a one-shot `langfuse-minio-init` container that creates the
  `langfuse` bucket via the `mc` client on first startup.

## Important: verify before you rely on this

This was hand-written against Langfuse's documented self-hosting reference
(cross-checked against `github.com/langfuse/langfuse/blob/main/docker-compose.yml`
at the time of writing), not copy-pasted with 100% certainty of exact
current values. Two specific things to double check against
**https://langfuse.com/self-hosting/docker-compose** before trusting this
beyond local experimentation, and both are called out as inline comments
in `compose.fragment.yml` too:

1. **Image tags.** This fragment pins `langfuse/langfuse:3` and
   `langfuse/langfuse-worker:3` per this exercise's spec (on the assumption
   that mirrors what prod is running). The upstream reference compose has
   already moved past `:3` for newer installs. **Match whatever your real
   prod Langfuse deployment is actually pinned to** — the goal of this
   local stack is fidelity to prod, not "latest."
2. **MinIO bucket creation.** This uses the classic `minio/minio` image
   plus a separate `mc`-based init container (`langfuse-minio-init`) to
   create the `langfuse` bucket, since that's the pattern named in this
   exercise's spec. Upstream's current reference compose instead uses a
   Chainguard minio image with an inline `mkdir -p /data/langfuse` startup
   command — functionally equivalent, different mechanism. Either works;
   don't be surprised if you see the other pattern in official docs.

All secrets in `compose.fragment.yml` (`NEXTAUTH_SECRET`, `SALT`,
`ENCRYPTION_KEY`, and the Postgres/ClickHouse/Redis/MinIO passwords) are
randomly generated placeholders for local dev only, committed in plain
text because this stack never leaves your machine. **Never reuse any of
them anywhere real.** Generate real ones with:

```bash
openssl rand -base64 32   # NEXTAUTH_SECRET, SALT
openssl rand -hex 32      # ENCRYPTION_KEY -- must be exactly 64 hex chars
```

## Running it — fully automated, zero manual UI steps

This used to require signing up, creating an org/project by hand, and
copy-pasting generated API keys into every service's env — that friction is
gone. `langfuse-web` is configured with Langfuse's own [headless
initialization](https://langfuse.com/self-hosting/administration/headless-initialization)
env vars (`LANGFUSE_INIT_ORG_ID`, `_PROJECT_ID`, `_PROJECT_PUBLIC_KEY`,
`_PROJECT_SECRET_KEY`, `_USER_EMAIL`, `_USER_PASSWORD`, etc. — see the root
`docker-compose.yml`), which auto-creates the org, project, admin user, and
a **fixed, known** API keypair on first boot. `llm-gateway` and
`agent-service` are pre-configured with that exact same keypair as their
default `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` — so tracing works the
moment this profile is up, with nothing to click through.

1. It's part of the default stack — `docker compose up -d` (from the repo root) starts it along with everything else. (Earlier in this project it briefly lived behind an opt-in `--profile langfuse` flag after some real memory-pressure crashes; re-verified stable since two other services' memory footprints got fixed, and moved back to default — see the root `README.md`'s "Verified" section for that history.)
2. Send any request through a traced service (`agent-service` or
   `llm-gateway` — see `../../docs/testing-and-navigation.md`).
3. Open **http://localhost:3000**, log in with the auto-created admin
   (`admin@aiops.local` / `localdev12345` — see `docker-compose.yml`'s
   `LANGFUSE_INIT_USER_*` values), and go to your project → **Traces**.
   Or skip the UI entirely and query the API directly with the same fixed
   keys:
   ```bash
   curl -u "pk-lf-a1b2c3d4-e5f6-4a1b-8c2d-000000000001:sk-lf-a1b2c3d4-e5f6-4a1b-8c2d-000000000002" \
     "http://localhost:3000/api/public/traces?limit=5"
   ```
   Confirmed working end to end during verification: both `llm-gateway`'s
   `chat-completion` trace (with token counts and calculated cost) and
   `agent-service`'s `agent-service.chat` trace (with session_id and
   latency) show up correctly.

**Want a real, non-local Langfuse instance instead** (a hosted account, or
your own self-hosted deployment)? Override
`LANGFUSE_HOST`/`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` in the root
`.env` — those override the fixed local defaults everywhere they're read.

## Notes

- `langfuse-redis` is exposed on host port **6380** (not 6379) to avoid
  clashing with any other local Redis you might already be running for a
  different service.
- MinIO's S3 API is on host port **9090**, its web console on **9091**
  (login with `minio` / the `MINIO_ROOT_PASSWORD` placeholder in the
  compose file) if you want to poke at the raw stored blobs.
- This stack does not attempt to replicate prod's IAM/network isolation —
  everything is on one docker network with plaintext placeholder
  credentials, which is appropriate for local dev only.
