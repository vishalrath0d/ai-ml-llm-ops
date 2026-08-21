# Multi-environment posture: dev / test / staging / prod

Everything in this project ran as a single, implicit "local dev" environment
until now — one `.env`, one set of defaults, CORS wide open, no auth, no
rate-limit awareness of environment. That's the right default for a
zero-setup learning project, but it's also exactly the posture that becomes
a real blocker the moment any of this stack is reachable from anywhere
other than your own laptop. This doc is the answer to "how do I actually
run this as dev/test/staging/prod," using nothing more exotic than env
vars — no new tooling, no separate codebases, no separate Dockerfiles per
environment.

## The one knob that drives everything else: `ENVIRONMENT`

Every one of the 4 custom Python services (`llm-gateway`, `agent-service`,
`rag-service`, `eval-service`) reads a single `ENVIRONMENT` variable —
`dev` (the default), `test`, `staging`, or `prod` — and uses it to pick
safe-by-default values for the two things that must NOT be the same in
every environment:

| Setting | `dev` default | anything else's default | override with |
|---|---|---|---|
| `CORS_ALLOWED_ORIGINS` | `*` (wide open) | nothing allowed | `CORS_ALLOWED_ORIGINS=https://your-real-ui.example.com` (comma-separated for more than one) |
| `REQUIRE_AUTH` | `false` | `true` | `REQUIRE_AUTH=true` / `REQUIRE_AUTH=false` (explicit override always wins over the environment-derived default) |

Nothing else in the codebase branches on `ENVIRONMENT` directly — it's
purely the thing these two defaults derive from. This is a deliberate,
narrow scope: it solves the two settings that are actively dangerous to
get wrong (an API reachable from any origin with no auth), without
pretending this project has a full environment-promotion pipeline it
doesn't actually have yet (see "What this does NOT give you" below).

## API-key auth (`API_KEY` / `REQUIRE_AUTH`)

Every one of the 4 services enforces one shared API key via
`ApiKeyMiddleware` (`../../services/*/app/auth.py` — same design in each,
adjusted to each service's own config style). Set `API_KEY` to a real
secret and either leave `REQUIRE_AUTH` unset (it'll default on for any
non-`dev` `ENVIRONMENT`) or set it explicitly:

```bash
API_KEY=some-long-random-string
REQUIRE_AUTH=true   # or leave unset if ENVIRONMENT is already staging/prod
```

Calls then need `X-API-Key: some-long-random-string`. Two routes are
exempt in every service, on purpose: `/health` (container healthchecks,
an orchestrator's liveness/readiness probes) and `/metrics` (Prometheus's
scraper) — a real deployment would more likely put those behind network
policy instead of an app-level exemption list; this is the honest,
simplest equivalent for a stack that runs on one docker-compose network
with no such policy layer. `OPTIONS` requests (CORS preflight) are exempt
too, for a different reason: a browser's preflight never carries a custom
header, so blocking it would break CORS for every real, correctly
authenticated request behind it.

This is deliberately one shared key, not per-client credentials or OAuth2
— the minimum bar the audit called for ("at minimum an API-key header
check"), not a full identity system. A real production deployment serving
more than one client would want per-client keys (so a compromised or
misbehaving client can be revoked individually) or a real OAuth2/service
mesh identity layer instead of one shared secret everyone holds.

## Rate limiting (`RATE_LIMIT_PER_MINUTE`)

`llm-gateway` — the single choke point every LLM call in this project
passes through, and the most expensive place per request to let a runaway
loop or retry storm run wild — enforces a per-client-IP requests/minute
cap via `../../services/llm-gateway/app/rate_limit.py`. Default is `60/minute`;
override with `RATE_LIMIT_PER_MINUTE=<n>`. This one is NOT
environment-derived (it applies the same regardless of `ENVIRONMENT`) —
unlike CORS/auth, there's no "dev" reason to want an unbounded gateway.
`/health` and `/metrics` are exempt from it too, same reasoning as auth.

## CORS (`CORS_ALLOWED_ORIGINS`)

Comma-separated list of origins allowed to call each service directly from
a browser (the pattern the web-ui relies on). Leave unset to get the
`ENVIRONMENT`-derived default from the table above; set it explicitly to
override in either direction (e.g. force a restrictive value even in dev,
or open it up temporarily in staging while debugging — not recommended to
leave that way).

## MLflow's `--allowed-hosts` (a third-party service, not app code)

`mlflow` itself isn't one of this project's 4 custom services, so it has no
`ENVIRONMENT` var to read -- but it has the exact same dev-vs-everything-else
problem: `--allowed-hosts "*"` (mlflow's own Host-header check, guarding
against DNS-rebinding) is fine to default to in `dev`, and explicitly
documented by MLflow itself as "not recommended for production." Set
`MLFLOW_ALLOWED_HOSTS` once mlflow exists in a real staging/prod environment:

```bash
MLFLOW_ALLOWED_HOSTS=mlflow,localhost   # exact hostnames actually calling it
```

## Setting these per environment

There's no separate `docker-compose.staging.yml` here — the same
`../../docker-compose.yml` is intentionally environment-agnostic; only the
`.env` file (or whatever your real deployment's secret/config injection
looks like — a Kubernetes ConfigMap+Secret pair, an ECS task definition's
environment block, etc.) changes between environments. Four example
profiles:

```bash
# .env (dev) -- this project's existing zero-setup default, unchanged
ENVIRONMENT=dev
# CORS_ALLOWED_ORIGINS, API_KEY, REQUIRE_AUTH all left unset

# .env.test -- CI / automated test runs (see ../../.github/workflows/ci.yml)
ENVIRONMENT=test
REQUIRE_AUTH=false          # tests hit services directly, no browser involved
LLM_PROVIDER_CHAIN=ollama   # free, offline, no hosted API key needed in CI secrets

# .env.staging
ENVIRONMENT=staging
REQUIRE_AUTH=true
API_KEY=<a real secret, injected via your CI/CD secret store, never committed>
CORS_ALLOWED_ORIGINS=https://staging-ui.your-domain.example.com

# .env.prod
ENVIRONMENT=prod
REQUIRE_AUTH=true
API_KEY=<a different real secret from staging's>
CORS_ALLOWED_ORIGINS=https://ui.your-domain.example.com
RATE_LIMIT_PER_MINUTE=120   # tune to real expected traffic
```

Run with a specific profile via `docker compose --env-file .env.staging up
-d`, or (more realistically for a real staging/prod deployment) inject
these as real environment variables/secrets from whatever your actual
deploy mechanism is — the app code doesn't know or care how the env var
arrived, only that `ENVIRONMENT`/`API_KEY`/etc. are set correctly by the
time the container starts.

## What this does NOT give you

Being honest about the scope here matters more than it sounds: this is
**environment-aware configuration**, not a full multi-environment
**deployment pipeline**. Specifically still missing, and out of scope for
this doc:

- **No environment-specific infrastructure.** There's still one
  `../../docker-compose.yml`; a real staging/prod would very likely run on
  different infrastructure entirely (a real Kubernetes cluster or ECS
  service, not docker-compose on one host), not just a differently-
  configured copy of the same compose file.
- **No promotion pipeline between environments.** Nothing here
  automatically moves a build from test → staging → prod on a gate passing
  — that's what `../../.github/workflows/ci.yml`'s eval gate is a *piece* of
  (the "should this be allowed to progress" check), not the whole pipeline.
- **No per-environment secret storage.** `API_KEY`/`GEMINI_API_KEY`/etc.
  living in a local `.env` file is correct for `dev` and fine for a
  personal `test` run; `staging`/`prod` secrets belong in a real secret
  manager (AWS Secrets Manager, Vault, a CI provider's encrypted secrets),
  never in a committed file — this project's `.env.example` stays the
  template with all values blank specifically so nothing real ever
  accidentally gets committed.
- **No environment-specific scaling/resource limits.** `../../docker-compose.yml`
  doesn't set replica counts or resource limits differently per
  environment; a real prod deployment would need those on infrastructure
  this project doesn't attempt to model (see
  `../concepts/04-finops-and-production-judgment.md` §11 for the scaling
  levers this would actually need).

Closing those gaps for real is a genuinely bigger, infrastructure-shaped
project (a real cluster, a real secret manager, a real CD pipeline) — this
doc closes the specific, concrete gap the audit flagged: the app code
itself no longer hardcodes a single environment's assumptions.
