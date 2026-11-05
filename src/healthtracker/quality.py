"""N5 / E2: measurement quality tiers (knowledge.feature_quality).

A poor-tier feature (sleep stage minutes, Body Battery, stress score) cannot drive an active
rule on its own, and no insight may rest on poor-tier measures alone. For sleep, the order of
trust is duration and timing, then my own sleep-quality rating, then stages.
"""

from __future__ import annotations

import psycopg

from healthtracker.db import fetch_all, fetch_one
from healthtracker.stats.hypothesis import parse_exposure


def tiers(conn: psycopg.Connection) -> dict[str, str]:
    return {r["feature"]: r["tier"] for r in fetch_all(conn, "SELECT feature, tier FROM knowledge.feature_quality")}


def poor_only(features: list[str] | None, tier: dict[str, str]) -> bool:
    """True when every listed feature is poor-tier (unknown features are not assumed poor)."""
    return bool(features) and all(tier.get(f) == "poor" for f in features)


def activation_problem(conn: psycopg.Connection, rule_id: str) -> str | None:
    rule = fetch_one(conn, "SELECT inputs FROM analytics.rule WHERE rule_id = %s", (rule_id,))
    if rule and poor_only(rule.get("inputs"), tiers(conn)):
        return f"{rule_id} rests only on poor-quality measures ({', '.join(rule['inputs'])}) and can't be made active"
    return None


def insight_problem(exposure: str, outcome: str, tier: dict[str, str]) -> str | None:
    col = parse_exposure(exposure)[0]
    poor = [f for f in (col, outcome) if tier.get(f) == "poor"]
    if poor:
        return f"rests on a poor-quality measure ({', '.join(poor)})"
    return None
