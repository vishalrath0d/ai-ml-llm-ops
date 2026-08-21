# SRE Practices for AI/Voice Systems

## Why this exists

Per the discussion in
[`concepts/03-reliability-debugging-ops.md`](concepts/03-reliability-debugging-ops.md)
(section 8, "SRE Practices for AI/Voice Systems"), it's common for an org
running LLM products to be missing all of the following:

- Incident-response runbooks
- Documented MTTD/MTTR tracking
- Blue-green or canary deployment automation
- Chaos-engineering practice of any kind

A typical deployment pipeline at this maturity level is straight-line:
`integration -> qa -> staging -> prod` via Jenkins, gated only by a manual
"choose the prod region" human step at the prod tag - that manual gate is
the closest thing to a release-safety control that exists anywhere in the
pipeline. This isn't a unique failing (most orgs running LLM products are
at a similar maturity level), but it's a concrete, fixable list rather
than an abstract one. This doc builds one artifact per item:

| Gap | Artifact |
|---|---|
| No incident runbooks | [Incident runbooks](#incident-runbooks) - two full runbooks: bad/hallucinated agent responses, and voice-latency degradation |
| No MTTD/MTTR tracking | [MTTD and MTTR tracking](#mttd-and-mttr-tracking) - concrete definitions, a log table template, and a worked example |
| No blue-green automation | [Blue-green deployment demo](#blue-green-deployment-demo) - [`blue_green_demo.sh`](../sre/blue_green_demo.sh) + [`nginx-bluegreen.conf`](../sre/nginx-bluegreen.conf) - a real, runnable local demo |
| No chaos-engineering practice | this project's `llm-gateway` chaos toggles - see [Chaos engineering](#chaos-engineering-the-projects-actual-tool-for-this) below |

## Incident runbooks

Two runbook templates, both closing the same gap called out above:
**none of the 15 surveyed AI repos have an incident-response runbook of
any kind.** Deployments today are a straight-line
integration -> qa -> staging -> prod pipeline gated only by a manual
"choose the prod region" step - that's the closest thing to a
release-safety control that exists, and there is nothing at all for "the
model is now live and doing something wrong."

Copy one of these into your own repo/wiki and fill in the
service-specific blanks (dashboard URLs, Slack channels, on-call rotation)
before you actually need it during an incident. A runbook you're reading
for the first time during an outage is worse than no runbook - the value
is in having walked the steps once, calmly, beforehand.

---

### Runbook 1: AI agent giving bad / hallucinated responses in production

**Applies to:** any LLM/agent service answering real user questions live
(this project's `agent-service`, or a production conversational-AI /
voice-AI equivalent).

#### Symptom / how you'd detect it

This is the sharpest incident-shape difference from a normal service
outage: **the request usually succeeds.** It returns a 200, latency looks
normal, no error is thrown - the response is just wrong. That means the
usual detection signals (error rate, 5xx count, crash logs) do not fire.
In practice you detect this one of two ways:

- **A live quality signal fires** - if you've wired `eval_pass_rate` (or
  a groundedness/faithfulness score) into your dashboards as an ongoing
  production SLO (not just a pre-deploy gate - see
  [`../ci-cd/README.md`](../ci-cd/README.md) for the pre-deploy half of
  this), a sustained drop below threshold on a rolling sample of live
  traffic is your earliest signal. This project's Grafana dashboard
  should have an `eval_pass_rate` panel for exactly this reason.
- **A human notices** - a customer or support agent flags a bad answer.
  Without a live quality signal, this is your *only* detection path, and
  it's the slower, less reliable one. This is why MTTD for this incident
  class is bounded by how fast someone notices, describes, and locates
  the exact conversation - see [MTTD and MTTR tracking](#mttd-and-mttr-tracking).

#### Immediate mitigation (do these in order, don't wait for root cause)

1. **Get the trace ID for the flagged conversation first**, before doing
   anything else - from the API response, a `/feedback`-style endpoint,
   or the eval-service run record. You need it intact before you start
   changing config, in case a mitigation step (like a rollback) makes the
   live state harder to reproduce.
2. **If it's isolated to one tool/capability** (e.g. one MCP tool
   returning bad data, one RAG source poisoning answers): disable that
   specific tool via a feature flag rather than rolling back the whole
   service. Narrower blast radius, faster to flip back once root-caused.
3. **If it's a recent prompt/model/config change**: flip back to the
   prior known-good version via your registry alias (MLflow alias flip -
   see `services/mlflow/` - or equivalent prompt-versioning indirection),
   *not* a full service redeploy, if your setup supports that separation.
   If it doesn't (a common real-world gap - see
   `concepts/03-reliability-debugging-ops.md` section 5 on
   baselines/rollback), redeploy the prior Docker image tag through the
   normal pipeline.
4. **If the LLM provider itself seems to be misbehaving** (garbled
   output, elevated latency, degraded quality across the board, not just
   one conversation): fail over to the secondary provider in your
   fallback chain (this project's `llm-gateway` supports multiple
   providers for this reason - flip `LLM_PROVIDER` or the gateway's
   configured fallback order).
5. **If the failure mode is severe or actively recurring and none of the
   above stops it fast enough**: degrade gracefully rather than leaving
   it live - route to a safer canned response, or hand off to a human,
   while you investigate. A live incident is not the time to debug a
   root cause in production; stop the bleeding first.

#### Diagnostic steps (in order)

1. **Open the trace** for the flagged conversation (Langfuse for this
   project; in a real production setup, a voice-AI backend and a
   text-chat backend might even use two different tracing tools). This
   is the single most important step - everything else is slower.
2. **Walk the chain, in order**: what did retrieval return? what
   tool(s) were called, with what arguments, and what did they return?
   what was the *exact final prompt* sent to the model (after retrieval
   and tool results were assembled)? what did the model generate?
3. **Identify which step diverged.** Common patterns, roughly in order of
   how often they turn out to be the cause:
   - Retrieval returned irrelevant/wrong documents (retrieval-relevance
     failure) -> the model was set up to hallucinate regardless of how
     good generation is.
   - Retrieval was fine but the answer isn't actually supported by the
     retrieved context (groundedness/faithfulness failure) -> the model
     ignored good context it was given.
   - A tool returned stale/incorrect data that the model then repeated
     as fact.
   - No RAG/tool involvement at all - the model just fabricated something
     from parametric knowledge (hardest to prevent, easiest to confirm
     once you're looking at the trace).
4. **Check whether this is a one-off or a pattern**: run the same or a
   similar input against `eval-service`'s scenario set, or grep recent
   traces for the same failure signature. One bad conversation is a
   support ticket; a pattern is an incident that needs the mitigation
   steps above, not just a fix-forward.
5. **Check the Grafana `eval_pass_rate` panel** for the relevant time
   window - did the score already show a dip before the human report
   came in? If yes, that's your MTTD number's honest answer: the signal
   existed before anyone acted on it.

#### Postmortem template

```
## Incident: <short title>

**Date/time detected:**
**Date/time resolved:**
**Detected by:** (live quality signal / customer report / support agent / other)
**Services affected:**
**Severity:** (e.g. isolated to one tool / systemic across all responses)

### What happened
<plain-language summary of the bad behavior a user actually saw>

### Root cause
<retrieval failure / groundedness failure / bad tool data / prompt
regression / model provider change / other - be specific, cite the trace>

### Trace link(s)
<Langfuse / LangSmith URL(s) for the representative bad conversation(s)>

### MTTD (mean time to detect)
<time from first bad response to first human/system awareness>

### MTTR (mean time to resolve)
<time from awareness to mitigation actually stopping the bad behavior>
(not "root cause fully understood" - see MTTD and MTTR tracking)

### Mitigation taken
<which of the immediate-mitigation steps above were used, in what order>

### Follow-up actions
- [ ] Add the failing case to the eval-service golden set so the CI eval
      gate (ci-cd/) would have caught a repeat of this before deploy
- [ ] <fix retrieval / fix tool / revert prompt / other root-cause fix>
- [ ] <any monitoring/alerting gap this incident exposed>
```

---

### Runbook 2: Voice agent latency degradation

**Applies to:** any real-time voice AI pipeline with per-turn latency
instrumentation - modeled on a production voice-AI backend, which
tracks STT latency, LLM time-to-first-token (TTFT), TTS first-audio
latency, and end-to-end (E2E) latency per turn, with P95 tracking exported
to Langfuse. If your local project's voice-shaped service exposes
comparable metrics, substitute its dashboard/trace links below.

#### Symptom / how you'd detect it

- **P95 (not P50/median) end-to-end per-turn latency crosses your SLO
  threshold** on the Grafana/Langfuse latency dashboard. P95 is the
  number that determines whether a phone call *feels* laggy - a healthy
  median can be hiding a badly-degraded tail that a chunk of real callers
  are experiencing right now.
- Customer/agent reports of calls feeling slow, laggy, or having long
  silences before the agent responds.
- A sustained (not single-spike) shift in any one of the four
  sub-latencies - see diagnostics below for why you check them in a
  specific order rather than just looking at the E2E number.

#### Immediate mitigation (do these in order)

1. **Confirm it's not a single bad host/instance first** - check whether
   the degradation is spread across all instances or concentrated on one.
   If concentrated: cordon/replace that instance before doing anything
   else, that alone may resolve it.
2. **If it correlates with a recent deploy**: roll back to the prior
   image tag/prompt-config version immediately. Don't wait for a full
   root cause - a fast rollback that turns out to be unnecessary costs a
   redeploy; a slow rollback while calls keep degrading costs live call
   quality.
3. **If the LLM provider's TTFT specifically has spiked** (isolate this
   with the diagnostic order below before assuming it's the whole
   pipeline): fail over to the secondary/fallback provider in
   `llm-gateway`'s configured chain rather than waiting for the primary
   provider to recover.
4. **If load/concurrency is the driver** (many simultaneous calls,
   approaching the box's known concurrent-call ceiling): this is a
   capacity problem, not a code-regression problem - see
   [`../load-testing/README.md`](../load-testing/README.md) for how to
   have already measured where that ceiling is *before* an incident, so
   you recognize this pattern immediately instead of debugging it live.
   Mitigate by shedding load (queue new calls, or fail new-call setup
   gracefully) rather than letting every in-progress call degrade
   together.
5. **Do not attempt a same-instance restart as your first move** for a
   stateful real-time voice service the way you might for a stateless
   API - an in-flight call is a live phone call holding a WebRTC/SIP
   session for its entire duration; restarting the instance drops every
   live call on it. See the [Blue-green deployment demo](#blue-green-deployment-demo)
   section for why draining, not just cutting over, is the correct pattern here.

#### Diagnostic steps (in this order - each step isolates where in the
pipeline the added time actually is, don't skip to E2E and guess)

1. **Open the per-turn latency dashboard first** (Grafana panel for this
   project; Langfuse for a production voice-AI backend) and look at
   P95 for all four legs side by side over the incident window: STT
   latency, LLM TTFT, TTS first-audio latency, E2E.
2. **Identify which leg moved.** This tells you where to look next
   without guessing:
   - **STT leg up** -> check the STT provider's own status page first,
     then audio-input quality/network path to STT.
   - **LLM TTFT up** -> check the LLM provider's status page, check
     whether prompt-caching is still hitting (a cache-prefix change -
     e.g. a prompt edit that moved something volatile earlier in the
     prompt - can silently kill your cache hit rate and make every call
     pay full prefix-processing cost again), check concurrent request
     volume against the provider's own rate limits.
   - **TTS leg up** -> check the TTS provider's status page; check
     whether streaming synthesis (starting audio from the first sentence
     rather than waiting for the full LLM response) is still actually
     wired up, since that's most of what keeps this leg small.
   - **All four legs proportionally up, or E2E up with no single leg
     dominating** -> look at infrastructure first: box CPU/memory
     saturation, connection-pool exhaustion, or the known concurrent-call
     ceiling being approached (see mitigation step 4 above).
3. **Check concurrent call count against the box's known ceiling** (a
   legacy voice-AI architecture might cap out around 20 concurrent calls
   per 8GB box due to per-call STT/LLM/TTS resource duplication - if your
   setup has a similarly-measured ceiling, check where you are relative
   to it before assuming a code regression).
4. **Cross-reference against the deploy timeline** - did this start right
   after a deploy, a prompt change, or a traffic-pattern shift (e.g. a
   marketing push driving call volume up)? Correlate before concluding
   causation, but a tight time correlation is a strong first hypothesis.

#### Postmortem template

```
## Incident: <short title>

**Date/time detected:**
**Date/time resolved:**
**Detected by:** (P95 alert / customer report / other)
**Peak concurrent calls during incident:**
**Which leg(s) degraded:** (STT / LLM TTFT / TTS / all proportionally)

### What happened
<plain-language summary - e.g. "P95 E2E latency rose from 900ms to 3.2s
for ~40 minutes, LLM TTFT leg specifically">

### Root cause
<provider degradation / capacity ceiling reached / cache-hit-rate
regression from a prompt change / bad deploy / other>

### Trace link(s)
<Langfuse trace URL(s) for representative degraded turns>

### MTTD (mean time to detect)

### MTTR (mean time to resolve)

### Mitigation taken

### Follow-up actions
- [ ] <capacity headroom fix / provider fallback tuning / cache-prefix fix>
- [ ] If this was a capacity ceiling: update the load-testing baseline in
      load-testing/README.md so the next incident is predicted, not
      discovered live
```

## MTTD and MTTR tracking

There's no log of past incidents, no record of how long it took to
notice or fix them, and therefore no way to answer "are we getting better
or worse at handling this class of problem over time" with a number
instead of a vibe. This section is the minimal thing that closes that
gap: two clear definitions, and a table template you actually fill in
every time an incident happens.

### MTTD - Mean Time to Detect

**Definition:** the time from when a problem *actually started affecting
users* to the time someone (a human or a system) *became aware of it.*

For a normal service, this is usually short and cheap to measure: an
error rate or crash spikes, an alert fires within seconds, done. For an
AI system it's structurally harder, and this is worth being precise
about rather than treating MTTD as one uniform number:

- **If you have a live quality signal** (an `eval_pass_rate`-style metric
  computed continuously against a rolling sample of live traffic, not
  just a pre-deploy gate - see [`../ci-cd/README.md`](../ci-cd/README.md)
  for the pre-deploy half), MTTD can be genuinely fast: minutes, bounded
  by your metric's sampling/aggregation window and alert threshold.
- **If you don't**, MTTD is bounded by how long it takes a human to
  notice something is wrong, describe it usefully, and someone to locate
  the exact conversation/trace - this is commonly hours, sometimes days.
  It's a common real-world gap: an org can have good hallucination-
  detection tooling that only runs offline, never on live traffic (see
  `concepts/03-reliability-debugging-ops.md` section 3).
- **A `trace_id`-to-feedback linkage** (the pattern a production
  conversational-AI system already has: a trace ID returned in the API response, reused as input to a
  `/feedback` endpoint) doesn't reduce the *time to notice* directly, but
  it dramatically reduces the time between "someone noticed" and "we know
  exactly which conversation and can see the whole trace" - which in
  practice is most of what makes MTTD painful without it.

### MTTR - Mean Time to Resolve

**Definition:** the time from *awareness* (the end of the MTTD clock) to
the problem *no longer affecting users* - i.e. the mitigation is live and
working, not necessarily "root cause fully understood and permanently
fixed."

This distinction matters more for AI incidents than for typical service
incidents: a rollback (MLflow alias flip, prior Docker image redeploy,
provider fallback) can stop a bad-response incident in minutes even though
the actual root cause (e.g. exactly why a particular RAG chunk caused a
particular hallucination pattern) might take days to fully understand.
**Stop the MTTR clock at mitigation, not at full root-cause
understanding** - track the deeper investigation as a follow-up action on
the incident row instead of blocking the MTTR number on it. Conflating the
two makes your MTTR numbers look artificially bad and discourages people
from reaching for fast mitigations (which is the wrong incentive).

### Incident log template

Copy this table into your own incident tracker (or just keep appending to
this file). One row per incident.

| Date | Service | Detected At | Resolved At | MTTD | MTTR | Root Cause | Trace Link |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

#### Worked example (fictional numbers, for format clarity only)

| Date | Service | Detected At | Resolved At | MTTD | MTTR | Root Cause | Trace Link |
|---|---|---|---|---|---|---|---|
| 2026-07-22 | agent-service | 14:12 UTC (support agent flagged a customer-reported wrong-refund-policy answer at 14:12; the bad responses actually started at 13:40 per later trace review) | 14:47 UTC (rolled back agent-service to prior prompt-config alias) | 32 min (13:40 -> 14:12) | 35 min (14:12 -> 14:47) | A same-day RAG source-doc update introduced a stale refund-policy chunk that ranked above the correct, updated chunk for common refund-policy phrasing - a retrieval-relevance failure, not a generation failure. Confirmed via Langfuse trace: retrieved context showed the stale chunk verbatim, and the model's answer was fully grounded in it (i.e. this was NOT a hallucination in the "model made something up" sense - it faithfully repeated bad retrieved context). | `https://traces.local/agent-service/trace/8f2a1c...` |

Reading this row the way you'd want to when triaging a similar future
incident: MTTD was 32 minutes because there was no live quality signal
yet at the time (this is exactly the kind of incident an `eval_pass_rate`
live-traffic SLO - see `../ci-cd/` and section 9 of the reliability doc -
would have caught closer to 13:40 instead of 14:12). MTTR was fast (35
min) specifically *because* the team reached for a prompt/config
rollback rather than trying to root-cause the exact stale-chunk ranking
issue live - that investigation became a follow-up action, not a blocker
on stopping the bleeding. The root-cause field is written the way a real
postmortem should be: specific enough that "retrieval-relevance failure,
not a hallucination" tells you something actionable (fix the doc
ingestion/ranking pipeline, not the model or prompt).

### What to actually do with this table

- Fill in a row for every incident that meets your runbook's definition of
  one (see [Incident runbooks](#incident-runbooks)) - including
  ones resolved quickly. A 10-minute incident is still worth one row; the
  point is to build a trend, not just document the bad ones.
- Periodically (monthly is reasonable to start) look at MTTD/MTTR *as a
  trend*, not per-incident. Is MTTD trending down after you added a live
  quality signal? Is MTTR staying flat because most incidents resolve via
  the same rollback pattern, or creeping up because rollbacks aren't
  available for a growing category of change? Those trend questions are
  the actual payoff of tracking this at all - a single incident's numbers
  don't tell you much on their own.
- Cross-reference the Trace Link column against actual Langfuse/LangSmith
  traces when writing the row - don't reconstruct root cause from memory
  days later, pull it from the trace while it's still fresh (see the
  [Incident runbooks](#incident-runbooks) diagnostic steps for how to walk
  a trace).

## Blue-green deployment demo

[`../sre/blue_green_demo.sh`](../sre/blue_green_demo.sh) is fully
self-contained - it does not depend on the sibling `services/`
microservices other agents are building. It spins up two copies of a
tiny dummy HTTP app ([`../sre/bluegreen_demo/app.py`](../sre/bluegreen_demo/app.py),
stdlib-only, no dependencies to install) on two ports, tagged `v1`/blue
and `v2`/green, fronts them with nginx using
[`../sre/nginx-bluegreen.conf`](../sre/nginx-bluegreen.conf), fires a
continuous stream of requests at the nginx front door, and flips the
upstream from blue to green **while that traffic is still running** by
rewriting one line of the nginx config and running `nginx -s reload`. It
then reports how many of those in-flight requests failed.

Run it:

```bash
../sre/blue_green_demo.sh
```

Expected output ends with something like:

```
Total requests fired while flipping: 80
Successful (HTTP 200):               80
Failed/dropped:                      0

PASS: zero dropped requests during the blue -> green flip.
```

It cleans up everything it started (nginx, both dummy processes, its
scratch temp dir) on exit, success or failure.

Note: the demo listens on `:8088`, not the more conventional `:8080` -
`:8080` collides with a locally-running Jenkins instance on the machine
this was built on; `:8088`/`:9101`/`:9102` were chosen as free ports.
Change `FRONT_PORT`/`BLUE_PORT`/`GREEN_PORT` at the top of the script if
any of those collide in your environment.

### Why this is the easy case

`nginx -s reload` works cleanly here because HTTP requests are short-lived
- each one completes in milliseconds, so "stop routing new requests to the
old version" and "the old version has no more work to do" happen almost
instantly. **This is specifically NOT true for a stateful real-time voice
service.** Per the reliability doc's section 8: for something like
a production voice-AI backend, an in-flight unit of work is a live phone call over
WebRTC/SIP that can run for **minutes**, holding open a LiveKit room and
STT/LLM/TTS provider connections for its entire duration. You cannot just
stop routing new calls to an old instance and kill it after a short drain
window the way this demo does - you have to wait out the actual duration
of every call still in progress on that instance, or you drop live
customer phone calls mid-conversation.

A correct rollout for that class of service needs explicit **call
draining**: mark an instance as not accepting new calls, let it keep
serving the calls already assigned to it until they naturally end, and
only then retire it. That's a meaningfully different (and harder) problem
than what `nginx -s reload` solves for free here - it needs the
application itself to expose a "draining" state and the deploy tooling to
poll "is this instance's active-call count zero yet" before terminating
it, rather than a fixed short timeout. Most web services never have to
solve this; it's specific to stateful real-time media services holding
long-lived sessions.

## Chaos engineering: the project's actual tool for this

Rather than building a separate chaos-injection mechanism, this
project's `llm-gateway` service (port 8001, built by another agent
working in parallel on `services/`) is the chaos-engineering practice
tool: it exposes runtime toggles for injected latency and error rate via
`POST /admin/chaos`.

Example - inject 2 seconds of latency into every gateway call:

```bash
curl -X POST http://localhost:8001/admin/chaos \
  -H 'Content-Type: application/json' \
  -d '{"CHAOS_LATENCY_MS": 2000, "CHAOS_ERROR_RATE": 0}'
```

To also inject a failure rate (e.g. 20% of calls return an error):

```bash
curl -X POST http://localhost:8001/admin/chaos \
  -H 'Content-Type: application/json' \
  -d '{"CHAOS_LATENCY_MS": 2000, "CHAOS_ERROR_RATE": 0.2}'
```

To turn chaos back off:

```bash
curl -X POST http://localhost:8001/admin/chaos \
  -H 'Content-Type: application/json' \
  -d '{"CHAOS_LATENCY_MS": 0, "CHAOS_ERROR_RATE": 0}'
```

### What to go watch while chaos is active

This is the actual exercise - injecting the fault is the easy part, the
point is to practice the diagnostic flow from the
[Incident runbooks](#incident-runbooks) with a fault you caused on
purpose and already know the cause of, before you have to do it live with
a fault you didn't cause and don't yet understand:

- [ ] **Grafana** - watch the latency panel for `llm-gateway` / the
      downstream `agent-service` calls that route through it. Confirm the
      injected latency actually shows up in P95, and notice which
      *other* panels move as a side effect (e.g. does `agent-service`'s
      own latency panel move too, showing the cost propagating downstream
      - this is the "tool-calling round-trip" cost described in the
      reliability doc's section 2, made visible).
- [ ] **Langfuse trace** for a request made while chaos is active - open
      it and confirm you can actually *see* where the injected delay/error
      landed in the trace (which span got slow, which call returned the
      injected error) - this is exactly the diagnostic skill the
      ["Voice agent latency degradation"](#runbook-2-voice-agent-latency-degradation)
      runbook asks you to exercise for a real incident, practiced here risk-free.
- [ ] **eval-service's `eval_pass_rate`** - if `CHAOS_ERROR_RATE` is high
      enough to cause scenario failures, confirm the eval gate in
      [`../ci-cd/`](../ci-cd/) would actually catch it: run
      `POST /scenarios/run-all` against `eval-service` while chaos is
      active and see the pass rate drop.
- [ ] **Try the runbook live**: with chaos active, walk the
      ["Voice agent latency degradation"](#runbook-2-voice-agent-latency-degradation)
      diagnostic steps in order and confirm they actually lead you to
      "check llm-gateway" as the answer - if they don't, that's a sign
      the runbook needs sharper diagnostic steps, not that the exercise
      failed.

This is the project's answer to "no chaos-engineering practice of any
kind" - a real, working fault-injection toggle on a real service, plus a
concrete checklist of what to observe while it's active, tying together
the Grafana dashboards, Langfuse tracing, and eval gate built elsewhere in
this project.
