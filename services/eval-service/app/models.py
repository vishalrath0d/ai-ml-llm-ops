"""ORM models: scenarios and test_runs.

Mirrors a typical production eval-service data model (there: Mongo documents; here:
Postgres tables, per this hands-on project's simplification).
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Scenario(Base):
    """A named eval scenario: an opening message to send to the agent under
    test, plus a semantic success_criteria the LLM judge will grade against.
    """

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    opening_message: Mapped[str] = mapped_column(Text, nullable=False)
    success_criteria: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    test_runs: Mapped[list["TestRun"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan"
    )


class TestRun(Base):
    """A single execution of a Scenario: the recorded transcript, the judge's
    verdict, and timing/metadata.
    """

    __tablename__ = "test_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_transcript: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    score_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    result: Mapped[str] = mapped_column(String(16), nullable=False)  # "pass" | "fail"
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    scenario: Mapped["Scenario"] = relationship(back_populates="test_runs")
