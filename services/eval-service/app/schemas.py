"""Pydantic request/response schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScenarioCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    opening_message: str = Field(..., min_length=1)
    success_criteria: str = Field(..., min_length=1)


class ScenarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    opening_message: str
    success_criteria: str
    created_at: datetime


class TestRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scenario_id: int
    conversation_transcript: list[dict[str, Any]]
    score_percent: float
    result: str
    reason: str
    latency_ms: int
    created_at: datetime
