"""Step runner: every pipeline step is logged to analytics.pipeline_run."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from healthtracker.db import connect, execute, fetch_one

log = logging.getLogger("healthtracker.pipeline")


@dataclass
class StepResult:
    step: str
    ok: bool
    rows: int = 0
    error: str | None = None


@dataclass
class JobReport:
    job: str
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    def summary(self) -> str:
        parts = [f"{s.step}:{'ok' if s.ok else 'FAILED'}({s.rows})" for s in self.steps]
        return f"{self.job} " + " ".join(parts)


def run_step(job: str, step: str, fn: Callable[[], int | None], report: JobReport) -> StepResult:
    """Run one step in its own transaction scope and log start, finish, and errors."""
    with connect() as conn:
        run_id = fetch_one(
            conn,
            "INSERT INTO analytics.pipeline_run (job, step) VALUES (%s, %s) RETURNING id",
            (job, step),
        )["id"]
    try:
        rows = fn() or 0
        result = StepResult(step, True, rows)
    except Exception as exc:  # noqa: BLE001 - logged and reported, job continues
        log.exception("step %s failed", step)
        tb = traceback.format_exc(limit=6)
        result = StepResult(step, False, 0, f"{type(exc).__name__}: {exc}\n{tb}")
    with connect() as conn:
        execute(
            conn,
            """UPDATE analytics.pipeline_run
               SET finished_at = now(), status = %s, rows_written = %s, error = %s
               WHERE id = %s""",
            ("ok" if result.ok else "failed", result.rows, result.error, run_id),
        )
    report.steps.append(result)
    log.info("%s/%s -> %s rows=%s", job, step, "ok" if result.ok else "FAILED", result.rows)
    return result
