# ci-cd/ — closing the "evals never gate a deploy" gap

## The gap, stated plainly

A lot of production CI/CD pipelines build Docker images, push to a
registry, register a deploy task, and deploy - straight-line,
branch/tag-gated, identical shape whether the service is a plain web
backend or an AI service. It's common for an LLM-as-judge eval service to
have a Jenkinsfile that is a **pure deploy pipeline**: build -> push ->
deploy, with no eval-execution stage anywhere in it. Evals get triggered
manually or on-demand via API, completely decoupled from the deploy
pipeline. A prompt, model-config, or generation-config change can go from
commit to production without anything automatically checking whether it
made answer quality worse.

Plenty of orgs already know how to build a real "must pass to merge" gate
for ordinary code — spin up service containers, run lint + type-check +
unit tests with a coverage floor, then integration tests, all as required
checks. That pattern just rarely gets extended to the thing that most
needs it for an AI system: treating an eval-score regression as a build
failure the same way a failing test or a coverage drop is treated.

(Full writeup: [`../docs/concepts/03-reliability-debugging-ops.md`](../docs/concepts/03-reliability-debugging-ops.md),
section 7, "CI/CD for AI Systems Specifically.")

## The CI/CD split: test-and-gate vs. deploy

Every pipeline here comes in two halves, one file per half, in both CI
systems this project demonstrates:

| | **Test / eval-gate half** | **Deployment half** |
|---|---|---|
| GitHub Actions (activated, in [`../.github/workflows/`](../.github/workflows/)) | [`ci.yml`](../.github/workflows/ci.yml) | [`deploy.yml`](../.github/workflows/deploy.yml) |
| GitHub Actions (annotated teaching copy, this folder) | [`github-actions-ci.yml`](./github-actions-ci.yml) | *(no separate teaching copy — `deploy.yml` is short enough to read directly)* |
| Jenkins (this folder — not wired to a real Jenkins server, reference-only) | [`example-Jenkinsfile.ci`](./example-Jenkinsfile.ci) | [`example-Jenkinsfile.deploy`](./example-Jenkinsfile.deploy) |

Splitting these was a deliberate choice, not just tidiness: the test/gate
half needs nothing but this repo and a Docker-capable runner, and is safe
to run on every PR for free. The deployment half needs real
registry/cloud credentials and only makes sense on a release tag — mixing
the two into one file either means the deploy stages sit unused on every
PR run, or the fast PR feedback loop gets coupled to slow, credential-gated
deploy logic. Two files, two purposes.

### Test / eval-gate half

**[`ci.yml`](../.github/workflows/ci.yml)** (GitHub Actions, live) runs on
every push/PR: a `strategy: matrix` job installs each service's own
`requirements.txt` + pytest into a fresh venv and runs its unit tests, then
a second job brings up the real stack via `docker compose`, waits for
health checks, and — the part a normal lint+test+coverage pipeline doesn't
have — calls `eval-service`'s `POST /scenarios/run-all`, reads
`eval_pass_rate` back out of its Prometheus `/metrics` endpoint, and
**fails the workflow** if the pass rate is below `0.8`. The gate step is
marked in the file with a large comment block:
`### THIS EVAL GATE IS THE PART A NORMAL LINT+TEST+COVERAGE PIPELINE DOESN'T HAVE.`

[`github-actions-ci.yml`](./github-actions-ci.yml) in this folder is the
*original* annotated teaching copy, kept unmodified on purpose: activating
it for real (moving it to `.github/workflows/`) surfaced one real bug the
activated `ci.yml` fixes — `docker compose exec <service> pytest` cannot
work, because each service's *runtime* image is a deliberately minimal
multi-stage build containing only `app/`, no `tests/` directory and no
pytest (confirmed directly: `docker exec llm-gateway which pytest` finds
nothing). That's correct image hygiene, not a bug to route around by
installing test tooling into a production image — `ci.yml` instead runs
each service's unit tests in the runner's own Python environment via a
`strategy: matrix` job, and only uses `docker compose` for what genuinely
needs the live, wired-together stack: the health-check wait and the eval
gate.

**[`example-Jenkinsfile.ci`](./example-Jenkinsfile.ci)** is the Jenkins
equivalent of `ci.yml`: a `matrix` block runs the same four services'
tests in parallel, then the same docker-compose-up → health-check →
eval-gate sequence, using the exact same threshold and metric-parsing
logic. Deliberately written with plain `sh` steps only, no shared-library
calls, so it's runnable on any Jenkins agent with Docker installed.

### Deployment half

**[`deploy.yml`](../.github/workflows/deploy.yml)** (GitHub Actions) is a
full staged rollout: build & push every service's image to GitHub
Container Registry (this part works out of the box, no extra secrets
needed — it uses the repo's own `GITHUB_TOKEN`), deploy to a `staging`
GitHub Environment, re-run the eval gate against that staging deployment
(not a local docker-compose stack this time), require a human approval
(GitHub Environments' required-reviewers protection rule — configure one
on a `production` environment and this workflow will actually pause and
wait), then deploy to production. The deploy steps past the build/push
stage are intentionally placeholders (`echo "Would deploy..."`) since
there's no real cloud infra behind this project — swap them for real
`aws ecs update-service` / `kubectl apply` / `terraform apply` calls once
pointed at real infra. The stage *shape* — build → staging → eval gate →
approval → production — is what's meant to be reused as-is.

**[`example-Jenkinsfile.deploy`](./example-Jenkinsfile.deploy)** — modeled
on a common production Jenkins pattern (build & tag → registry push →
register a deploy task → deploy, shared-library style, branch-gated lower
environments, tag-gated + manual-input-gated prod). One stage is inserted
beyond what a typical deploy pipeline has, clearly marked: an **Eval
Gate** stage that runs after the staging deploy and before the production
approval step, calling `eval-service` the same way the GitHub Actions
version does and failing the build (via Jenkins' `error()` step, which
halts the pipeline) on a regression. Like `deploy.yml`, this needs a real
shared Jenkins library (`shared-ci-library`, providing
`sharedEcrLogin`/`sharedEcsDeploy`) and real registry/cloud credentials to
actually deploy anywhere — without them it'll fail past the build/push
stages, which is expected. The point of this file is showing how you'd
retrofit the eval gate into a pipeline's *existing* style, not just how
you'd build one greenfield.

All four eval-gate stages use the same threshold (`0.8`) and the same
parsing approach: `curl` the eval-service endpoints, then a small inline
Python script that reads the Prometheus exposition format line-by-line
looking for a metric name of `eval_pass_rate`, extracts the trailing
numeric value, and exits non-zero if it's under threshold.

## Running it

All four files assume `llm-gateway` (8001), `rag-service` (8002),
`agent-service` (8003), and `eval-service` (8004) each expose a `/health`
endpoint, and that `eval-service` exposes `POST /scenarios/run-all` and a
Prometheus `/metrics` endpoint containing an `eval_pass_rate` sample. In
CI, set `LLM_PROVIDER_CHAIN=ollama` (or an equivalent mocked/local mode in
`llm-gateway`) so runs are free and offline - never point a PR-triggered
pipeline at a paid hosted provider by default, since cost then scales
with PR volume.

## Why this matters

Deterministic code has a compiler and a test suite: the same input
produces the same output, so "tests pass" is a strong signal that a change
didn't break anything. An LLM/agent system doesn't have that guarantee -
the same prompt against the same model can produce different outputs
across runs, a prompt edit that improves the five examples you eyeballed
can regress the hundred you didn't, and a provider can silently change the
underlying model out from under you with no code change on your end. A
passing `pytest` run tells you the code path executed without throwing;
it tells you nothing about whether the *answer* is still good.

That's exactly why an eval-gated pipeline is the direct AI-system analog
of "run unit tests, block merge on failure": the golden eval set is your
test cases, the eval pass rate is your pass/fail signal, and a fixed
threshold (or, in a more mature setup, a fixed baseline score - see
[`../docs/operations/sre-practices.md`](../docs/operations/sre-practices.md) and the reliability
doc's section 5 on baselines/rollback) is what you're diffing against.
Skipping this gate doesn't mean AI quality regressions don't happen - it
means they happen silently, in production, and you find out from a
customer complaint or the next scheduled offline eval run instead of from
a blocked merge.
