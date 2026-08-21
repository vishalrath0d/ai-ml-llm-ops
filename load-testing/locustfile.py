"""
locustfile.py - load tests for the hands-on AI-ops project's services.

Two HttpUser classes:

  - AgentServiceUser  hammers agent-service's  POST /chat            (port 8003)
  - GatewayUser       hammers llm-gateway's    POST /v1/chat/completions (port 8001)

Plus a RampingConcurrencyShape (a Locust LoadTestShape) that steps
concurrent users up through several stages so you can plot a
latency-vs-throughput curve instead of a single flat number - see
README.md for exactly what to look at while it runs and how to read the
resulting curve.

Usage (pick ONE target host - Locust only talks to one --host per run):

    # Hit agent-service directly:
    locust -f locustfile.py --host http://localhost:8003

    # Hit llm-gateway directly:
    locust -f locustfile.py --host http://localhost:8001

Then open http://localhost:8089 for the web UI, or run headless - see
README.md for both.
"""

import json
import random

from locust import HttpUser, LoadTestShape, between, task

# A grab-bag of varied fake user messages so requests aren't all
# byte-for-byte identical (identical requests can hit a cache path and
# make your load test measure the cache instead of the real pipeline).
FAKE_USER_MESSAGES = [
    "What's the status of my order #48213?",
    "I want to cancel my subscription, how do I do that?",
    "Can you explain your refund policy in plain English?",
    "My SMS messages aren't sending, can you help troubleshoot?",
    "How much does the pro plan cost per month?",
    "I was charged twice this month, can you check that?",
    "What integrations do you support with Salesforce?",
    "Is there a free trial available?",
    "How do I add a new team member to my account?",
    "Can you walk me through setting up a new campaign?",
    "What's the difference between your basic and premium tiers?",
    "I need to update my billing address.",
    "Do you support two-factor authentication?",
    "How long does support usually take to respond?",
    "Can I export my conversation history?",
]


def _random_message():
    return random.choice(FAKE_USER_MESSAGES)


class AgentServiceUser(HttpUser):
    """Simulates a real end user chatting with agent-service."""

    # Real users pause between turns to read a response and type the next
    # one - a few seconds is more realistic than hammering back-to-back,
    # and keeping this nonzero avoids the load test measuring something
    # no real traffic pattern looks like.
    wait_time = between(1, 4)

    @task(9)
    def chat(self):
        payload = {
            "session_id": f"loadtest-{random.randint(1, 100000)}",
            "message": _random_message(),
        }
        with self.client.post(
            "/chat",
            json=payload,
            name="/chat",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"unexpected status {resp.status_code}: {resp.text[:200]}")

    @task(1)
    def health_check(self):
        # Low-weight background traffic - mimics load balancer / uptime
        # checks that share the same instance capacity as real chat
        # traffic, without dominating the request mix.
        self.client.get("/health", name="/health")


class GatewayUser(HttpUser):
    """Hits llm-gateway directly, bypassing agent-service's orchestration.

    Useful for isolating "is the gateway/model call itself the
    bottleneck" from "is agent-service's own orchestration overhead the
    bottleneck" - run this class against llm-gateway's host separately
    from AgentServiceUser and compare the two latency curves.
    """

    wait_time = between(0.5, 2)

    @task
    def chat_completion(self):
        payload = {
            "model": "default",
            "messages": [
                {"role": "system", "content": "You are a helpful support assistant."},
                {"role": "user", "content": _random_message()},
            ],
            "max_tokens": 200,
            "temperature": 0.7,
        }
        with self.client.post(
            "/v1/chat/completions",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            name="/v1/chat/completions",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"unexpected status {resp.status_code}: {resp.text[:200]}")


class RampingConcurrencyShape(LoadTestShape):
    """Steps concurrent users up through fixed stages to trace out a
    latency-vs-throughput curve rather than a single number at one fixed
    concurrency level.

    This directly demonstrates the "what happens under load" /
    latency-throughput-tradeoff exercise: as concurrent users climb, RPS
    should climb roughly linearly at first, then P95 latency should start
    climbing as some resource (LLM provider rate limit, DB connection
    pool, CPU) saturates - that inflection point is the number you're
    actually trying to find.

    This shape is picked up automatically by Locust when present in a
    locustfile - run headless with just `-f locustfile.py --host <url>`
    (no --users/--spawn-rate needed, this class controls that), or select
    it from the web UI's shape dropdown.

    To disable it and drive concurrency manually from the web UI instead,
    just comment this class out or run with `--headless -u N -r R` and no
    LoadTestShape present.
    """

    # (duration_seconds_from_start, target_user_count, spawn_rate)
    stages = [
        (30, 5, 1),      # warm up: 5 concurrent users
        (60, 20, 2),     # ramp to 20
        (90, 50, 5),     # ramp to 50 - watch for the latency knee here
        (120, 100, 10),  # ramp to 100 - likely past the knee for a single instance
        (150, 20, 5),    # ramp back down - confirms recovery, not just onset
    ]

    def tick(self):
        run_time = self.get_run_time()
        for duration, users, spawn_rate in self.stages:
            if run_time < duration:
                return (users, spawn_rate)
        return None  # stop the test after the last stage
