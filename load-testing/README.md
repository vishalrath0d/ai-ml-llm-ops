# load-testing/ — the concrete "how do we compare performance" exercise

## Why this folder exists

Per [`../../02-concepts/03-reliability-debugging-ops.md`](../../02-concepts/03-reliability-debugging-ops.md)
(section 1), for a conversational AI system "performance" isn't one
number - it's latency (broken into per-stage percentiles, not just an
average), cost per unit of value, quality (eval pass-rate), and throughput
measured as *concurrent sessions*, not raw requests/second. A production
voice-AI backend is a good example of actually quantifying a scaling change before
shipping it (e.g. going from ~20 concurrent calls/8GB box to a target of 200+) instead of
guessing. This folder is the hands-on version of that exercise: generate
real load against this project's services, watch the latency/throughput
curve as concurrency climbs, and correlate what you see against the
Grafana dashboard and Langfuse traces for the same time window - the same
loop a production voice-AI team would have used to validate their scaling
redesign actually landed.

## What's in `locustfile.py`

Two `HttpUser` classes:

- **`AgentServiceUser`** - hits `agent-service`'s `POST /chat` (port 8003)
  with a rotating set of realistic fake support messages, plus a small
  amount of `GET /health` background traffic. `wait_time` is
  `between(1, 4)` seconds, modeling a real user pausing to read a
  response before typing the next message.
- **`GatewayUser`** - hits `llm-gateway`'s `POST /v1/chat/completions`
  (port 8001) directly, bypassing `agent-service`'s orchestration
  entirely. `wait_time` is `between(0.5, 2)` seconds.

Running these against their respective services separately lets you
isolate **"is the model call itself the bottleneck"** (`GatewayUser`
against `llm-gateway`) from **"is agent-service's own orchestration
overhead (routing, tool calls, RAG lookups) the bottleneck"**
(`AgentServiceUser` against `agent-service`) - the same kind of
isolation the reliability doc's section 2 describes for diagnosing
where latency actually hides in an agent pipeline.

There's also a **`RampingConcurrencyShape`** (a Locust `LoadTestShape`).
When present in the locustfile, Locust auto-detects and uses it instead
of manual `-u`/`-r` flags. It steps concurrent users through fixed
stages - 5 -> 20 -> 50 -> 100 -> back down to 20 - over about 150 seconds,
which is what actually produces a latency-vs-throughput *curve* instead of
a single number at one arbitrarily-chosen concurrency level. The point is
to find the inflection point: RPS should climb roughly linearly with
concurrency at first, then P95 latency should start climbing sharply once
some resource saturates (an LLM provider rate limit, a DB connection pool,
CPU) - that knee in the curve is the number you're actually looking for,
not "how fast is it at 5 users."

## How to run it

Install dependencies (a plain venv is enough, no other setup needed):

```bash
cd load-testing
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Locust only targets one `--host` per run, so pick one of the two services
to load-test at a time:

```bash
# Against agent-service directly:
locust -f locustfile.py --host http://localhost:8003

# Against llm-gateway directly:
locust -f locustfile.py --host http://localhost:8001
```

Then open the web UI at **http://localhost:8089**, click "Start
swarming" (the `RampingConcurrencyShape` will drive concurrency
automatically - you don't need to fill in the user-count/spawn-rate
fields, though Locust still asks for them as a starting point), and watch
the live charts.

To run headless (e.g. in CI, or for a quick scripted run) with a CSV
report instead of the web UI:

```bash
locust -f locustfile.py --host http://localhost:8003 --headless --csv results
```

(The `RampingConcurrencyShape` still controls concurrency and overall
duration in headless mode - `-u`/`-r`/`--run-time` flags are ignored
while a `LoadTestShape` class is present in the file. Comment the class
out if you want manual control instead.)

## What numbers to look for

- **RPS (requests/second)** - should climb roughly in step with the
  concurrent-user count during the early ramp stages. If RPS flattens out
  or drops while concurrency keeps climbing, you've found your ceiling -
  the service can't push more throughput no matter how many more
  concurrent callers you add.
- **P95 response time, not the average** - per the reliability doc's
  framing, P95 (not median) is the number that determines whether the
  experience "feels laggy" to your worst-treated users. Watch specifically
  for the point where P95 starts climbing away from P50 - that divergence
  is the earliest sign of saturation, usually visible before RPS actually
  flattens.
- **Failure rate** - should stay at 0% through the lower concurrency
  stages. A nonzero failure rate appearing partway through the ramp (not
  from the start, which would suggest a config/connectivity problem
  instead) is a second, sharper signal that you've passed the service's
  real capacity - requests are now timing out or being rejected outright
  rather than just getting slower.

## Correlating a load-test run against the rest of the project

This is the actual "compare performance" exercise - a load test in
isolation just gives you Locust's own numbers; the payoff is
cross-referencing those numbers against what the rest of this project's
observability stack shows for the *same time window*:

1. **Start the run, note the wall-clock start time.**
2. **Open the Grafana dashboard's latency panels** for the service you're
   hitting (`llm-gateway` or `agent-service`) while the test runs. Confirm
   the panel's latency trend visually matches what Locust's own chart is
   showing for the same window - if they diverge (e.g. Locust shows P95
   climbing but Grafana's panel looks flat), that's worth investigating on
   its own; it usually means the two are measuring different points in the
   pipeline (client-observed latency including network hops vs.
   server-side processing time only).
3. **Open a Langfuse trace for a request made during the high-concurrency
   stage** (the 50 or 100-concurrent-user stage) and compare it against a
   trace from the low-concurrency warmup stage. Look specifically at
   whether the *shape* of the trace changed - e.g. did an individual LLM
   call's TTFT get slower under load (provider-side queueing), or did the
   same call stay fast but something upstream (queueing before it even
   reached the model call) grew instead? That distinction tells you
   whether the bottleneck is the model provider or your own
   service/infra, the same diagnostic split described in
   [`../docs/sre-practices.md`](../docs/sre-practices.md#chaos-engineering-the-projects-actual-tool-for-this)'s chaos-engineering section.
4. **If you have `eval-service`'s `eval_pass_rate` dashboarded**, check
   whether quality held steady under load or degraded - a service that
   starts timing out mid-generation and returning truncated/degraded
   responses under load is a quality regression that pure latency/RPS
   numbers won't show you on their own.
5. **Repeat the same run after a change** (a config tweak, a connection-pool
   size change, a provider swap) and diff the two curves rather than the
   two single numbers - this is the same "before/after with a fixed
   measurement, not a vibe" discipline the reliability doc's section 1
   and section 5 (baselines) both argue for, applied to a load test
   instead of an eval run.
