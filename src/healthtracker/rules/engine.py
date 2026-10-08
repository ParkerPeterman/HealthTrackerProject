"""Run every non-retired rule for a day and write analytics.daily_flag."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import psycopg

from healthtracker.db import fetch_all, upsert
from healthtracker.jsonutil import to_jsonable
from healthtracker.rules import catalog  # noqa: F401 - registers the rules
from healthtracker.rules.base import REGISTRY, RuleContext, RuleResult
from healthtracker.rules.context import build_context

log = logging.getLogger("healthtracker.rules")


@dataclass
class Evaluated:
    rule_id: str
    domain: str
    status: str  # rule lifecycle: shadow | active
    description: str
    evidence_level: str
    result: RuleResult

    @property
    def visible(self) -> bool:
        return self.status == "active"


def evaluate(ctx: RuleContext, rules: list[dict]) -> list[Evaluated]:
    out = []
    for meta in rules:
        spec = REGISTRY.get(meta["rule_id"])
        if spec is None:
            continue
        if ctx.history_days < meta["min_history_days"]:
            result = RuleResult(False, status="dormant",
                                details={"history_days": ctx.history_days,
                                         "required": meta["min_history_days"]})
        else:
            try:
                result = spec.fn(ctx, ctx.params.get(meta["rule_id"], {}))
            except Exception as exc:  # noqa: BLE001 - one bad rule must not stop the rest
                log.exception("rule %s failed", meta["rule_id"])
                result = RuleResult(False, status="error", details={"error": f"{type(exc).__name__}: {exc}"})
        for a in result.actions:
            if a.outcome_metric is None:
                a.outcome_metric = spec.outcome
        out.append(Evaluated(meta["rule_id"], meta["domain"], meta["status"], meta["description"],
                             meta["evidence_level"], result))
    return out


def run_rules(conn: psycopg.Connection, d: date) -> tuple[RuleContext, list[Evaluated]]:
    rules = fetch_all(conn, "SELECT * FROM analytics.rule WHERE status <> 'retired' ORDER BY rule_id")
    ctx = build_context(conn, d)
    results = evaluate(ctx, rules)
    rows = [{
        "date": d,
        "rule_id": e.rule_id,
        "fired": e.result.fired,
        "severity": e.result.severity if e.result.fired else None,
        "details": {**to_jsonable(e.result.details), "eval_status": e.result.status},
    } for e in results]
    upsert(conn, "analytics.daily_flag", rows, conflict=["date", "rule_id"])
    return ctx, results

