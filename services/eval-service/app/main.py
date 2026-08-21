"""eval-service FastAPI app.

Mirrors a typical eval-service design: define Scenarios, drive a live conversation
against the agent under test (this project's sibling agent-service), then
have an LLM judge (via the sibling llm-gateway) grade the transcript
pass/fail against the scenario's success_criteria.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import metrics, models, schemas
from app.agent_client import AgentServiceError, run_conversation
from app.auth import ApiKeyMiddleware
from app.config import CORS_ALLOWED_ORIGINS, ENVIRONMENT, SEED_ON_STARTUP
from app.database import Base, SessionLocal, engine, get_db
from app.judge import JudgeError, call_judge
from app.logging_setup import configure_logging
from app.tracing import trace_eval_run

logger = logging.getLogger("eval_service")
configure_logging(ENVIRONMENT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Postgres may not be accepting connections yet if this container starts
    # before the shared postgres service is ready; retry briefly rather than
    # crash-looping.
    for attempt in range(1, 11):
        try:
            Base.metadata.create_all(bind=engine)
            break
        except OperationalError as exc:
            logger.warning("database not ready yet (attempt %s/10): %s", attempt, exc)
            await asyncio.sleep(2)
    else:
        raise RuntimeError("could not connect to the database after 10 attempts")

    db = SessionLocal()
    try:
        metrics.recompute_gauges(db)
        if SEED_ON_STARTUP:
            from app.seed import seed_if_empty

            inserted = seed_if_empty(db)
            if inserted:
                logger.info("seeded %s demo scenario(s)", inserted)
                metrics.recompute_gauges(db)
    finally:
        db.close()

    yield


app = FastAPI(title="eval-service", version="1.0.0", lifespan=lifespan)

# CORS_ALLOWED_ORIGINS defaults to "*" only in dev (see app/config.py) --
# outside dev it defaults to allowing nothing until set explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Added AFTER CORSMiddleware -- see agent-service/app/main.py's identical
# comment for why that ordering matters (CORS preflight must run first).
app.add_middleware(ApiKeyMiddleware)


@app.get("/health")
def health():
    return {"status": "ok", "service": "eval-service"}


@app.get("/metrics")
def metrics_endpoint():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/scenarios", response_model=schemas.ScenarioOut, status_code=201)
def create_scenario(payload: schemas.ScenarioCreate, db: Session = Depends(get_db)):
    scenario = models.Scenario(
        name=payload.name,
        opening_message=payload.opening_message,
        success_criteria=payload.success_criteria,
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return scenario


@app.get("/scenarios", response_model=list[schemas.ScenarioOut])
def list_scenarios(db: Session = Depends(get_db)):
    return db.query(models.Scenario).order_by(models.Scenario.id).all()


@app.get("/scenarios/{scenario_id}", response_model=schemas.ScenarioOut)
def get_scenario(scenario_id: int, db: Session = Depends(get_db)):
    scenario = db.get(models.Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"scenario {scenario_id} not found")
    return scenario


async def _execute_run(scenario: models.Scenario, db: Session) -> models.TestRun:
    """Drives the conversation, calls the judge, and persists a TestRun.

    Infra failures (agent-service unreachable, judge call/parse failure) are
    recorded as a failed TestRun rather than raised as a 5xx -- an eval run
    that can't reach its target is itself a meaningful (failing) eval result,
    and this keeps /run-all resilient to one bad scenario.
    """
    start = time.perf_counter()
    transcript: list[dict] = []
    try:
        transcript = await run_conversation(scenario.opening_message)
        verdict = await call_judge(scenario.success_criteria, transcript)
        score_percent, result, reason = verdict.score_percent, verdict.result, verdict.reason
    except AgentServiceError as exc:
        score_percent, result, reason = 0.0, "fail", f"agent-service error: {exc}"
    except JudgeError as exc:
        score_percent, result, reason = 0.0, "fail", f"judge error: {exc}"

    latency_ms = int((time.perf_counter() - start) * 1000)

    test_run = models.TestRun(
        scenario_id=scenario.id,
        conversation_transcript=transcript,
        score_percent=score_percent,
        result=result,
        reason=reason,
        latency_ms=latency_ms,
    )
    db.add(test_run)
    db.commit()
    db.refresh(test_run)

    metrics.eval_run_latency_seconds.labels(scenario=scenario.name).observe(latency_ms / 1000.0)
    metrics.eval_runs_total.labels(scenario=scenario.name, result=result).inc()
    metrics.recompute_gauges(db)
    trace_eval_run(
        scenario_name=scenario.name,
        opening_message=scenario.opening_message,
        success_criteria=scenario.success_criteria,
        transcript=transcript,
        score_percent=score_percent,
        result=result,
        reason=reason,
        latency_ms=latency_ms,
    )

    return test_run


@app.post("/scenarios/{scenario_id}/run", response_model=schemas.TestRunOut)
async def run_scenario(scenario_id: int, db: Session = Depends(get_db)):
    scenario = db.get(models.Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"scenario {scenario_id} not found")
    return await _execute_run(scenario, db)


@app.post("/scenarios/run-all", response_model=list[schemas.TestRunOut])
async def run_all_scenarios(db: Session = Depends(get_db)):
    scenarios = db.query(models.Scenario).order_by(models.Scenario.id).all()
    if not scenarios:
        raise HTTPException(
            status_code=404, detail="no scenarios exist yet; create one with POST /scenarios"
        )
    results = []
    for scenario in scenarios:
        results.append(await _execute_run(scenario, db))
    return results


@app.get("/scenarios/{scenario_id}/results", response_model=list[schemas.TestRunOut])
def get_results(scenario_id: int, db: Session = Depends(get_db)):
    scenario = db.get(models.Scenario, scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"scenario {scenario_id} not found")
    return (
        db.query(models.TestRun)
        .filter(models.TestRun.scenario_id == scenario_id)
        .order_by(models.TestRun.created_at.desc())
        .all()
    )
