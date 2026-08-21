"""Prometheus metrics exposed at GET /metrics.

These are the series the project's Grafana dashboard (built by another agent)
is expected to visualize:
  - eval_pass_rate      (gauge, 0-1, per scenario + "overall")
  - eval_avg_score      (gauge, 0-100, per scenario + "overall")
  - eval_run_latency_seconds (histogram, per scenario)
  - eval_runs_total     (counter, labeled by result: pass/fail)
"""
from prometheus_client import Counter, Gauge, Histogram

eval_pass_rate = Gauge(
    "eval_pass_rate",
    "Fraction (0-1) of eval test runs that passed. label scenario='overall' "
    "aggregates across all scenarios; other label values are per-scenario.",
    ["scenario"],
)

eval_avg_score = Gauge(
    "eval_avg_score",
    "Average judge score_percent (0-100) across eval test runs. label "
    "scenario='overall' aggregates across all scenarios; other label values "
    "are per-scenario.",
    ["scenario"],
)

eval_run_latency_seconds = Histogram(
    "eval_run_latency_seconds",
    "Wall-clock latency of a single scenario run (agent-service conversation "
    "+ llm-gateway judge call), in seconds.",
    ["scenario"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
)

eval_runs_total = Counter(
    "eval_runs_total",
    "Total number of eval test runs executed, labeled by result (pass/fail).",
    ["scenario", "result"],
)


def recompute_gauges(db) -> None:
    """Recomputes eval_pass_rate / eval_avg_score from persisted TestRun rows.

    Called after every run and once at startup, so the gauges always reflect
    full history in Postgres -- not just in-process counters -- which matters
    since they should read correctly even right after a process restart.
    """
    from app.models import Scenario, TestRun  # local import avoids a circular import

    scenarios = db.query(Scenario).all()
    all_scores: list[float] = []
    all_results: list[str] = []

    for scenario in scenarios:
        runs = db.query(TestRun).filter(TestRun.scenario_id == scenario.id).all()
        if not runs:
            continue
        scores = [r.score_percent for r in runs]
        results = [r.result for r in runs]
        all_scores.extend(scores)
        all_results.extend(results)

        pass_count = sum(1 for r in results if r == "pass")
        eval_pass_rate.labels(scenario=scenario.name).set(pass_count / len(results))
        eval_avg_score.labels(scenario=scenario.name).set(sum(scores) / len(scores))

    if all_results:
        overall_pass = sum(1 for r in all_results if r == "pass")
        eval_pass_rate.labels(scenario="overall").set(overall_pass / len(all_results))
        eval_avg_score.labels(scenario="overall").set(sum(all_scores) / len(all_scores))
