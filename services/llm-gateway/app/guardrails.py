"""Inline guardrails -- fast, deterministic checks run on EVERY request, in
the critical path, before a provider is called and again before a response
is returned.

This is the one "check on every single generation" pattern that's genuinely
realistic in production (see the root README's "How it all works" section 1
sequence diagram for exactly where this fires, and section 3 for a
side-by-side comparison against online eval and eval-service):
cheap regex/keyword classifiers, not another LLM call. Contrast with:

  * app/tracing.py (this service) / agent-service's online_eval.py: an LLM
    JUDGE call, sampled, asynchronous, AFTER the response already went out --
    grading quality, never blocking anything.
  * eval-service: a full scenario-based LLM judge, offline/on-demand --
    testing, not a runtime behavior.

Guardrails here do the opposite of both: they're deliberately cheap enough to
run on every request inline, and they can actually block/redact before
anything reaches the caller or an upstream provider. A real deployment would
likely swap these hand-rolled regexes for a dedicated moderation API or a
small classifier model, but the architecture -- check inline, block or
redact, log it -- is the same either way.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

# ---------------------------------------------------------------------------
# Prompt-injection / jailbreak heuristics (input guardrail only -- these are
# attempts to manipulate the MODEL, meaningless to check for in its output)
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    re.compile(r"ignore (all|any|the) (previous|prior|above) instructions", re.I),
    re.compile(r"disregard (your|the) (system prompt|instructions|rules)", re.I),
    re.compile(r"you are now (dan|jailbroken|unrestricted|in developer mode)", re.I),
    re.compile(r"pretend (you have|to have) no (restrictions|rules|guidelines)", re.I),
    re.compile(r"reveal your (system prompt|instructions|hidden prompt)", re.I),
    re.compile(r"act as if you have no content policy", re.I),
]

# ---------------------------------------------------------------------------
# PII heuristics (both directions -- don't forward sensitive input to a
# provider, and don't let a response leak sensitive data back out either)
# ---------------------------------------------------------------------------

_CANDIDATE_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _luhn_valid(candidate: str) -> bool:
    """Luhn checksum -- lets us tell an actual credit-card-shaped number
    apart from any other 13-19 digit sequence (order IDs, phone numbers)
    without a false-positive on every long number in a message."""
    digits = [int(ch) for ch in candidate if ch.isdigit()]
    if len(digits) < 13:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


@dataclass
class GuardrailResult:
    triggered: bool
    guardrail: Optional[str] = None
    detail: Optional[str] = None


def _check_prompt_injection(text: str) -> GuardrailResult:
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return GuardrailResult(True, "prompt_injection", f"matched pattern: {pattern.pattern!r}")
    return GuardrailResult(False)


def _check_pii(text: str) -> GuardrailResult:
    for match in _CANDIDATE_CARD_RE.finditer(text):
        if _luhn_valid(match.group(0)):
            return GuardrailResult(True, "pii_credit_card", "credit-card-like number detected")
    if _SSN_RE.search(text):
        return GuardrailResult(True, "pii_ssn", "SSN-like number detected")
    return GuardrailResult(False)


def check_input(messages: List[dict]) -> GuardrailResult:
    """Run all input-side guardrails against the latest user message. Only
    the latest user turn is checked (not the whole history) so an injection
    attempt several turns back that the model already didn't act on doesn't
    keep tripping the guardrail on every subsequent unrelated message."""
    user_texts = [m.get("content") or "" for m in messages if m.get("role") == "user"]
    if not user_texts:
        return GuardrailResult(False)
    latest = user_texts[-1]

    result = _check_prompt_injection(latest)
    if result.triggered:
        return result
    return _check_pii(latest)


def check_output(text: str) -> GuardrailResult:
    """Run output-side guardrails against a generated response. Only PII
    applies here -- a response can leak sensitive data, but "prompt
    injection" isn't a meaningful thing to detect in a model's own output."""
    return _check_pii(text)


REFUSAL_MESSAGE = "I can't help with that request."
REDACTION_MESSAGE = "[This response was withheld because it appeared to contain sensitive personal information.]"
