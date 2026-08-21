# sre/ — blue-green deployment demo

This folder holds one thing: a real, runnable demonstration of a
zero-downtime blue-green cutover. For the broader SRE practices this
project also covers (incident runbooks, MTTD/MTTR tracking, and how to
use chaos injection as a practice tool), see
[`../docs/operations/sre-practices.md`](../docs/operations/sre-practices.md)
— this file is specifically about the code in this folder and how to run
it.

## Files

- **`blue_green_demo.sh`** — the demo script, fully self-contained. It
  does not depend on any of the sibling `services/` microservices.
- **`bluegreen_demo/app.py`** — a tiny dummy HTTP app (stdlib-only, no
  dependencies to install) that the script runs two copies of, tagged
  `v1`/blue and `v2`/green.
- **`nginx-bluegreen.conf`** — the nginx config the script rewrites (one
  line) and reloads to flip traffic from blue to green.

## What it does

`blue_green_demo.sh` spins up two copies of the dummy app on two ports,
fronts them with nginx using `nginx-bluegreen.conf`, fires a continuous
stream of requests at the nginx front door, and flips the upstream from
blue to green **while that traffic is still running** by rewriting one
line of the nginx config and running `nginx -s reload`. It then reports
how many of those in-flight requests failed.

## Running it

```bash
./blue_green_demo.sh
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

Note: the demo listens on `:8088`, not the more conventional `:8080` —
`:8080` collides with a locally-running Jenkins instance on the machine
this was built on; `:8088`/`:9101`/`:9102` were chosen as free ports.
Change `FRONT_PORT`/`BLUE_PORT`/`GREEN_PORT` at the top of the script if
any of those collide in your environment.

## Why this is the easy case

`nginx -s reload` works cleanly here because HTTP requests are short-lived
— each one completes in milliseconds, so "stop routing new requests to the
old version" and "the old version has no more work to do" happen almost
instantly. **This is specifically NOT true for a stateful real-time voice
service.** Per [`../docs/concepts/03-reliability-debugging-ops.md`](../docs/concepts/03-reliability-debugging-ops.md)
section 8: for something like a production voice-AI backend, an in-flight
unit of work is a live phone call over WebRTC/SIP that can run for
**minutes**, holding open a LiveKit room and STT/LLM/TTS provider
connections for its entire duration. You cannot just stop routing new
calls to an old instance and kill it after a short drain window the way
this demo does — you have to wait out the actual duration of every call
still in progress on that instance, or you drop live customer phone calls
mid-conversation.

A correct rollout for that class of service needs explicit **call
draining**: mark an instance as not accepting new calls, let it keep
serving the calls already assigned to it until they naturally end, and
only then retire it. That's a meaningfully different (and harder) problem
than what `nginx -s reload` solves for free here — it needs the
application itself to expose a "draining" state and the deploy tooling to
poll "is this instance's active-call count zero yet" before terminating
it, rather than a fixed short timeout. Most web services never have to
solve this; it's specific to stateful real-time media services holding
long-lived sessions.
