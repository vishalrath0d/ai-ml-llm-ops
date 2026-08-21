"""OpenAI-compatible request/response schemas for /v1/chat/completions,
plus the admin chaos-injection config schema."""
from __future__ import annotations

import time
import uuid
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field


class ToolCallFunction(BaseModel):
    name: str
    arguments: str  # JSON-encoded string -- OpenAI wire format, even though
    # Ollama's native API hands back (and expects) a parsed dict. Providers
    # are responsible for converting to/from their own backend's shape; this
    # schema only speaks the one wire format every caller in this project
    # already expects (langchain_openai's ChatOpenAI parses this exact shape).


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    temperature: Optional[float] = 1.0
    stream: Optional[bool] = False
    tools: Optional[List[dict]] = None
    tool_choice: Optional[Union[str, dict]] = None


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionChoiceMessage(BaseModel):
    role: str = "assistant"
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatCompletionChoiceMessage
    finish_reason: str = "stop"


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:24]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[ChatCompletionChoice]
    usage: Usage


class ChaosConfigRequest(BaseModel):
    """Body for POST /admin/chaos. Both fields optional -- send only the one
    you want to change."""

    latency_ms: Optional[int] = Field(default=None, ge=0, description="Milliseconds to sleep before handling a request")
    error_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Probability (0-1) of a synthetic 500")


class ChaosConfigResponse(BaseModel):
    chaos: dict
