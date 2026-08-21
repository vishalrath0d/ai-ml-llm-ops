"""LLM-as-judge: builds the judge prompt and calls llm-gateway to grade a
transcript against a scenario's success_criteria.

This mirrors a common production LLM-as-judge prompt pattern: the
success_criteria is treated as a SEMANTIC reference describing the qualities a
correct response must have, not a literal script to string-match. Paraphrases,
different wording/ordering, and extra (correct) helpful detail are all fine as
long as the substance of the criteria is satisfied. This is essential because
conversational agent outputs are open-ended -- exact-match grading would fail
correct answers just for being phrased differently.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import HTTP_TIMEOUT_SECONDS, JUDGE_MODEL, LLM_GATEWAY_URL

logger = logging.getLogger("eval_service.judge")


class JudgeError(Exception):
    """Raised when the judge call fails, or the judge's output can't be
    parsed into a verdict."""


@dataclass
class JudgeVerdict:
    score_percent: float
    result: str  # "pass" | "fail"
    reason: str


JUDGE_SYSTEM_PROMPT = """You are an impartial evaluation judge for a conversational AI agent's responses.

You will be given:
1. SUCCESS CRITERIA: a description of what a correct/acceptable agent response looks like.
2. An ACTUAL CONVERSATION TRANSCRIPT between a user and the agent under test.

IMPORTANT: the success criteria is a SEMANTIC REFERENCE describing the qualities, facts, or
behaviors a correct response must have. It is NOT a literal script the agent must match
word-for-word. Paraphrasing, different wording, different ordering, a different tone, or
extra (correct) helpful detail are all fine as long as the underlying substance of the
criteria is satisfied. Do not penalize the agent for failing to use the same words as the
criteria -- judge only whether the sense and substance of the criteria was met by the
transcript.

Score the transcript from 0 to 100 reflecting how fully the success criteria was satisfied,
then decide pass/fail: result should be "pass" when the criteria was satisfied in substance
(typically score_percent >= 70), otherwise "fail".

Respond with STRICT JSON ONLY. No markdown code fences, no commentary before or after the
JSON, no trailing text. Respond in exactly this shape:

{"score_percent": <integer 0-100>, "result": "pass" or "fail", "reason": "<one or two sentence explanation citing what in the transcript drove the verdict>"}
"""


def build_judge_messages(success_criteria: str, transcript: list[dict[str, Any]]) -> list[dict[str, str]]:
    transcript_text = "\n".join(
        f"{str(turn.get('role', 'unknown')).upper()}: {turn.get('content', '')}" for turn in transcript
    )
    user_prompt = (
        f"SUCCESS CRITERIA (semantic reference, not a script to match verbatim):\n{success_criteria}\n\n"
        f"ACTUAL CONVERSATION TRANSCRIPT:\n{transcript_text}\n\n"
        "Evaluate whether the agent's responses in the transcript satisfy the success criteria "
        "in substance. Return the strict JSON verdict now."
    )
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_judge_response(raw_content: str) -> JudgeVerdict:
    """Parses the judge model's raw text output into a JudgeVerdict.

    Defensive by design: judge models sometimes wrap JSON in prose or code
    fences despite instructions, so we extract the first {...} block rather
    than requiring the entire response to be pure JSON.
    """
    if not raw_content or not raw_content.strip():
        raise JudgeError("empty judge response")

    match = _JSON_BLOCK_RE.search(raw_content)
    if not match:
        raise JudgeError(f"no JSON object found in judge response: {raw_content[:200]!r}")

    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise JudgeError(f"judge response was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise JudgeError(f"judge JSON was not an object: {payload!r}")

    if "score_percent" not in payload or "result" not in payload:
        raise JudgeError(f"judge JSON missing required fields (score_percent/result): {payload!r}")

    try:
        score = float(payload["score_percent"])
    except (TypeError, ValueError) as exc:
        raise JudgeError(f"score_percent not numeric: {payload.get('score_percent')!r}") from exc
    score = max(0.0, min(100.0, score))

    result = str(payload["result"]).strip().lower()
    if result not in ("pass", "fail"):
        # Defensive normalization for near-miss labels (e.g. "PASSED"); fall
        # back to a score-derived result rather than raising, since we still
        # have a usable score.
        result = "pass" if score >= 70 else "fail"

    reason = str(payload.get("reason", "")).strip() or "no reason provided by judge"

    return JudgeVerdict(score_percent=score, result=result, reason=reason)


async def call_judge(success_criteria: str, transcript: list[dict[str, Any]]) -> JudgeVerdict:
    """Calls the sibling llm-gateway's OpenAI-compatible /v1/chat/completions
    endpoint with the judge prompt, and parses the verdict.
    """
    messages = build_judge_messages(success_criteria, transcript)
    payload = {
        "model": JUDGE_MODEL,
        "messages": messages,
        "temperature": 0,
    }
    url = f"{LLM_GATEWAY_URL.rstrip('/')}/v1/chat/completions"

    # Retry ONLY a transport-level failure (connection refused/reset, DNS,
    # etc. -- httpx.TransportError), never an HTTP status error. This
    # distinction matters specifically because llm-gateway is itself a
    # multi-provider fallback gateway (see services/llm-gateway/app/
    # main.py) -- a non-2xx response means it already tried every
    # configured provider and still failed, so retrying THAT would
    # re-walk the entire already-exhausted chain again, exactly the
    # compounding-latency bug found and fixed in agent-service/app/
    # graph.py (max_retries=0, with the same rationale in its comment).
    # A transport error, by contrast, means llm-gateway was never even
    # reached -- a plain transient blip a retry can actually help with.
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()
    except httpx.TransportError as exc:
        logger.warning("llm-gateway request failed (attempt 1/2): %s -- retrying once", exc)
        await asyncio.sleep(0.5)
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                body = resp.json()
        except httpx.HTTPError as exc2:
            raise JudgeError(f"llm-gateway request failed: {exc2}") from exc2
        except ValueError as exc2:
            raise JudgeError(f"llm-gateway returned a non-JSON body: {exc2}") from exc2
    except httpx.HTTPError as exc:
        raise JudgeError(f"llm-gateway request failed: {exc}") from exc
    except ValueError as exc:
        raise JudgeError(f"llm-gateway returned a non-JSON body: {exc}") from exc

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise JudgeError(f"unexpected llm-gateway response shape: {body!r}") from exc

    return parse_judge_response(content)
