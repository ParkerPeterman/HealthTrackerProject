"""Weekly: propose rule parameter changes from my own data, within bounds.

Nothing changes until I approve a proposal in the app (or CLI). Every proposal
and decision is kept in analytics.rule_parameter_history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import psycopg
from scipy import stats as sps

from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.stats.hypothesis import ALPHA, MIN_EFFECT_SD, Hypothesis, build_frame, fit_hac
from healthtracker.stats.insights import load_features_frame


@dataclass
class Proposal:
    rule_id: str
    name: str
    value: float
    reason: str


def _best_threshold(features: pd.DataFrame, column: str, op: str, candidates, outcome: str, lag: int,
                    sign: int, min_n: int = 15) -> tuple[float, float, float] | None:
    """Threshold whose exposure best separates the outcome (max |t|), if significant and harmful."""
    best = None
    for c in candidates:
        h = Hypothesis(0, f"{column} {op} {c}", outcome, lag)
        df, _ = build_frame(features, h)
        if df.empty or min((df["E"] == 1).sum(), (df["E"] == 0).sum()) < min_n:
            continue
        eff, lo, hi, p = fit_hac(df)
        sd = df["Y"].std()
        if sign * eff <= 0 or p >= ALPHA or (sd > 0 and abs(eff) / sd < MIN_EFFECT_SD):
            continue
        t = abs(eff) / ((hi - lo) / 3.92)
        if best is None or t > best[2]:
            best = (float(c), float(eff), float(t))
    return best


def _effect(features: pd.DataFrame, h: Hypothesis, keep=None) -> tuple[float, float, float, int, int] | None:
    df, _ = build_frame(features, h)
    if keep is not None:
        df = df[keep(df)]
    n_e, n_u = int((df["E"] == 1).sum()), int((df["E"] == 0).sum())
    if min(n_e, n_u) < 5:
        return None
    eff, _, _, p = fit_hac(df)
    sd = float(df["Y"].std())
    return eff, p, (eff / sd if sd > 0 else 0.0), n_e, n_u


def propose_caffeine_cutoff(features: pd.DataFrame, current: float, reference_before: float = 12.0,
                            min_n: int = 15) -> Proposal | None:
    """The hour h that best splits harmless from harmful caffeine for that night's sleep score.

    For each h: caffeine at/after h must hurt (vs. days with none after 12:00) and caffeine in
    [12:00, h) must not. Among those, pick the h with the largest before/after contrast.
    """
    last = features["last_caffeine_hour"].astype(float)
    best = None
    for h in range(int(reference_before), 19):
        harm = _effect(features, Hypothesis(0, f"last_caffeine_hour >= {h}", "sleep_score", 1),
                       keep=lambda df, h=h: ~last.reindex(df.index).between(reference_before, h, inclusive="left"))
        if not harm or min(harm[3], harm[4]) < min_n or not (harm[0] < 0 and harm[1] < ALPHA
                                                               and abs(harm[2]) >= MIN_EFFECT_SD):
            continue
        window = _effect(features, Hypothesis(0, f"last_caffeine_hour >= {reference_before}", "sleep_score", 1),
                         keep=lambda df, h=h: ~(last.reindex(df.index) >= h))
        harmless_before = window is None or not (window[0] < 0 and window[1] < 0.10
                                                 and abs(window[2]) >= MIN_EFFECT_SD)
        contrast = (window[0] if window else 0.0) - harm[0]
        if harmless_before and (best is None or contrast > best[2]):
            best = (float(h), harm[0], contrast)
    if not best or best[0] == current:
        return None
    return Proposal("SLEEP_CAFFEINE_CUTOFF", "cutoff_hour", best[0],
                    f"Caffeine before {int(best[0])}:00 shows no effect on your sleep, but at or after it your "
                    f"sleep score that night averages {best[1]:+.1f} points.")


def propose_short_sleep(features: pd.DataFrame, current: float) -> Proposal | None:
    best = _best_threshold(features, "sleep_total_min", "<", range(360, 481, 15), "energy", 0, sign=-1)
    if not best or best[0] == current:
        return None
    return Proposal("SLEEP_SHORT", "min_minutes", best[0],
                    f"Nights under {int(best[0]) // 60}h{int(best[0]) % 60:02d} separate next-day energy best "
                    f"({best[1]:+.2f} on the 1-5 scale).")


def propose_post_game_days(features: pd.DataFrame, current: float) -> Proposal | None:
    """How many days after a game is my HRV still clearly below baseline?"""
    f = features.sort_index()
    games = f.index[f["had_game"].fillna(False).astype(bool)]
    if len(games) < 8:
        return None
    suppressed = 0
    means = []
    for k in (1, 2, 3):
        vals = f["hrv_z_30d"].reindex(games + pd.Timedelta(days=k)).dropna().astype(float)
        if len(vals) < 8:
            break
        m, se = vals.mean(), vals.std(ddof=1) / np.sqrt(len(vals))
        upper = m + sps.t.ppf(0.975, len(vals) - 1) * se
        means.append(round(float(m), 2))
        if m <= -0.25 and upper < 0:
            suppressed = k
        else:
            break
    proposed = float(min(3, max(1, suppressed)))
    if proposed == current or not means:
        return None
    return Proposal("TRAIN_POST_GAME", "recovery_days", proposed,
                    f"HRV vs baseline on days 1-{len(means)} after games: {means} SD; "
                    f"suppressed for {suppressed} day(s).")


TUNERS = {
    ("SLEEP_CAFFEINE_CUTOFF", "cutoff_hour"): propose_caffeine_cutoff,
    ("SLEEP_SHORT", "min_minutes"): propose_short_sleep,
    ("TRAIN_POST_GAME", "recovery_days"): propose_post_game_days,
}


def run_tuning(conn: psycopg.Connection, as_of: date) -> int:
    features = load_features_frame(conn, as_of)
    if features.empty or len(features) < 28:
        return 0
    n = 0
    for (rule_id, name), tuner in TUNERS.items():
        param = fetch_one(conn, "SELECT * FROM analytics.rule_parameter WHERE rule_id = %s AND name = %s",
                          (rule_id, name))
        if not param:
            continue
        prop = tuner(features, float(param["value"]))
        if prop is None:
            continue
        lo, hi = param["min_allowed"], param["max_allowed"]
        value = min(max(prop.value, lo if lo is not None else prop.value), hi if hi is not None else prop.value)
        if value == float(param["value"]):
            continue
        pending = fetch_one(conn, """SELECT id FROM analytics.rule_parameter_history
                                     WHERE rule_id = %s AND name = %s AND approved IS NULL""", (rule_id, name))
        if pending:
            execute(conn, """UPDATE analytics.rule_parameter_history SET new_value = %s, reason = %s,
                             changed_at = now() WHERE id = %s""", (value, prop.reason, pending["id"]))
        else:
            insert(conn, "analytics.rule_parameter_history", {
                "rule_id": rule_id, "name": name, "old_value": param["value"], "new_value": value,
                "reason": prop.reason, "proposed_by": "tuning_job", "approved": None}, returning=None)
        n += 1
    return n


def decide_parameter(conn: psycopg.Connection, history_id: int, approve: bool) -> dict:
    row = fetch_one(conn, "SELECT * FROM analytics.rule_parameter_history WHERE id = %s", (history_id,))
    if not row or row["approved"] is not None:
        raise ValueError("no pending proposal with that id")
    if approve:
        execute(conn, """UPDATE analytics.rule_parameter SET value = %s, updated_at = now(), updated_by = %s
                         WHERE rule_id = %s AND name = %s""",
                (row["new_value"], row["proposed_by"], row["rule_id"], row["name"]))
    execute(conn, "UPDATE analytics.rule_parameter_history SET approved = %s, changed_at = now() WHERE id = %s",
            (approve, history_id))
    return row


def set_parameter(conn: psycopg.Connection, rule_id: str, name: str, value: float, reason: str = "manual") -> None:
    """Manual change from settings; logged like everything else."""
    param = fetch_one(conn, "SELECT * FROM analytics.rule_parameter WHERE rule_id = %s AND name = %s",
                      (rule_id, name))
    if not param:
        raise ValueError("unknown parameter")
    execute(conn, """UPDATE analytics.rule_parameter SET value = %s, updated_at = now(), updated_by = 'manual'
                     WHERE rule_id = %s AND name = %s""", (value, rule_id, name))
    insert(conn, "analytics.rule_parameter_history", {
        "rule_id": rule_id, "name": name, "old_value": param["value"], "new_value": value,
        "reason": reason, "proposed_by": "manual", "approved": True}, returning=None)


def pending_parameter_proposals(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, """SELECT h.*, p.min_allowed, p.max_allowed, r.description
                              FROM analytics.rule_parameter_history h
                              JOIN analytics.rule_parameter p USING (rule_id, name)
                              JOIN analytics.rule r USING (rule_id)
                              WHERE h.approved IS NULL ORDER BY h.changed_at DESC""")
