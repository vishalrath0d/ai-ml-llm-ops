"""HTTP client that drives a short conversation against the sibling
agent-service, the system under test.

Request contract assumed (agent-service is owned by a different agent in this
hands-on project, built in parallel): POST {AGENT_SERVICE_URL}/chat with body

    {"message": str, "session_id": str, "history": [{"role": "user"|"assistant", "content": str}, ...]}

The response is parsed defensively: any of a handful of common reply keys
(response/reply/message/text/content/answer) is accepted, or a plain-text
body, so this keeps working even if the sibling's exact JSON shape differs
slightly from this guess.
"""
import asyncio
import logging
import uuid
from typing import Any

import httpx

from app.config import AGENT_SERVICE_URL, HTTP_TIMEOUT_SECONDS, MAX_CONVERSATION_TURNS

logger = logging.getLogger("eval_service.agent_client")


class AgentServiceError(Exception):
    """Raised when agent-service is unreachable or returns something we
    cannot interpret as a reply."""


# Generic follow-up prompts used to extend a scenario into a short multi-turn
# conversation (turn 2+). Kept content-neutral so they apply to any scenario.
FOLLOW_UP_PROMPTS = [
    "Can you confirm that, and share any relevant details or sources?",
    "Is there anything else I should know about that?",
]


def _extract_reply(body: Any, raw_text: str) -> str:
    if isinstance(body, dict):
        for key in ("response", "reply", "message", "text", "content", "answer"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value
    if raw_text and raw_text.strip():
        return raw_text.strip()
    raise AgentServiceError(f"could not extract a reply from agent-service response: {body!r}")


async def run_conversation(opening_message: str, turns: int | None = None) -> list[dict[str, str]]:
    """Drives `turns` user turns against agent-service starting with
    opening_message, returning the full transcript as a list of
    {"role": "user"|"assistant", "content": str} dicts.
    """
    turns = turns or MAX_CONVERSATION_TURNS
    turns = max(1, turns)
    session_id = str(uuid.uuid4())
    transcript: list[dict[str, str]] = []
    url = f"{AGENT_SERVICE_URL.rstrip('/')}/chat"

    next_message = opening_message
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        for turn_index in range(turns):
            transcript.append({"role": "user", "content": next_message})
            payload = {
                "message": next_message,
                "session_id": session_id,
                "history": transcript[:-1],
            }
            # One retry after a short, fixed backoff -- cheap cover for a
            # transient connection blip during a multi-turn scenario run,
            # same reasoning as agent-service's tools_impl.py/
            # feature_store_client.py. A run that fails after both
            # attempts still raises AgentServiceError, same as before.
            try:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("agent-service request failed (attempt 1/2): %s -- retrying once", exc)
                await asyncio.sleep(0.5)
                try:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                except httpx.HTTPError as exc2:
                    raise AgentServiceError(f"agent-service request failed: {exc2}") from exc2

            try:
                body = resp.json()
            except ValueError:
                body = None
            reply = _extract_reply(body, resp.text)
            transcript.append({"role": "assistant", "content": reply})

            if turn_index + 1 < turns:
                next_message = FOLLOW_UP_PROMPTS[turn_index % len(FOLLOW_UP_PROMPTS)]

    return transcript
