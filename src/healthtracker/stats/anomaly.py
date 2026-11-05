"""G4: explaining unusual days from their structured record.

An unusual morning is one whose nightly HRV, resting HR, or sleep duration sits at least
`min_z` (2) robust SDs from my own trailing 28 days (median and MAD, the day itself left out,
at least 14 days). Each one is explained, in order, from:

  1. data quality (A4): the watch wasn't worn overnight, or the night isn't plausible. The
     reading itself may be off, so this comes first.
  2. context flags (A4, E5): sick, the possible-illness pattern, travel, a time-zone or
     daylight-saving change, a stress tag.
  3. my own effects (B1): for each credible estimate (P(meaningful) >= 0.6) with this outcome,
     whether its exposure was there at the hypothesis's lag, and how much it would move the
     outcome: the estimate for a yes/no exposure, the estimate per unit times how far the dose
     was above my usual for a dose. One estimate per exposure (the dose one first), so
     nothing is counted twice.
  4. anything else unusual the day before (at least 1.5 SDs from my usual) with no estimate
     yet, listed as "also unusual", without a claimed effect.

The explained share is how much of the deviation the effects in its direction add up to
(capped at 100%). The sentence is rendered by code from the record. Medication never
appears here (its findings live in H3).

Unusual days of the past week, and G3 shifts of the past four weeks, become 'anomaly'
candidates for the weekly ranking (C3).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg
from scipy import stats

from healthtracker.db import execute, fetch_all, fetch_df, upsert
from healthtracker.medication import is_sensitive
from healthtracker.stats.changepoint import LOGS
from healthtracker.stats.hypothesis import LABELS, Hypothesis, describe, parse_exposure
from healthtracker.units import fmt_hour

DEFAULTS = {"min_z": 2.0, "baseline_days": 28, "min_baseline": 14, "min_p": 0.6, "other_z": 1.5}
OUTCOMES = {"hrv": ("HRV", "ms", "%.0f"), "resting_hr": ("resting HR", "bpm", "%.0f"),
            "sleep_total_min": ("sleep", "min", "%.0f")}
QUALITY = {"watch_overnight": "the watch wasn't worn overnight", "sleep_plausible": "the night's sleep record looks implausible"}
CONTEXT = {"is_sick": "a sick day", "is_travel": "a travel day", "tz_change": "a time-zone change",
           "dst_week": "a daylight-saving change", "tag_stress": "a stressful day"}
OTHERS = {"alcohol_drinks": "drinks", "caffeine_mg": "caffeine", "last_caffeine_hour": "late caffeine",
          "last_meal_hour": "a late last meal", "session_load": "a big training load", "train_end_hour": "late training",
          "water_late_ml": "late water", "bedtime_hour": "a late bedtime"}
ILLNESS_RULE = "RECOV_POSSIBLE_ILLNESS"
DOSE_WORDS = {"alcohol_drinks": lambda x: f"{x:g} drink{'' if x == 1 else 's'}",
              "caffeine_mg": lambda x: f"{x:.0f} mg of caffeine",
              "caffeine_hours_before_bed": lambda x: f"caffeine {x:.1f} h before bed",
              "bedtime_hour": lambda x: f"bedtime at {fmt_hour(x)}",
              "training_min": lambda x: f"{x:.0f} min of training"}
BINARY_WORDS = {"load_week_change_pct": "a jump in weekly load", "session_load": "a big session",
                "acwr": "a load spike", "had_game": "a game"}
MINOR = 0.1  # effects under a tenth of the deviation stay in the record but not in the sentence


def robust_z(s: pd.Series, days: int, min_n: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Each day against the trailing `days` days before it: (z, median, robust SD)."""
    past = s.shift(1).rolling(days, min_periods=min_n)
    med = past.median()
    mad = s.shift(1).rolling(days, min_periods=min_n).apply(lambda x: np.nanmedian(np.abs(x - np.nanmedian(x))), raw=True)
    sd = (1.4826 * mad).where(lambda v: v > 0)
    return (s - med) / sd, med, sd


def credible_effects(effects: list[dict], outcome: str, min_p: float) -> dict[str, list[dict]]:
    """Credible estimates for this outcome, grouped by exposure column, in the order to try them:
    the dose estimate first, then yes/no thresholds from the most extreme. The first that
    applies on a day is the one used, so nothing is counted twice."""
    out: dict[str, list[dict]] = {}
    for e in effects:
        if e["outcome"] != outcome or e["estimate"] is None or (e["p_meaningful"] or 0) < min_p:
            continue
        col, op, val = parse_exposure(e["exposure"])
        out.setdefault(col, []).append({**e, "_op": op, "_val": val})
    for items in out.values():
        items.sort(key=lambda e: (e["kind"] != "dose",
                                  -(e["_val"] or 0) if e["_op"] in (">=", ">") else (e["_val"] or 0)))
    return out


def _flag(v) -> bool | None:
    """True / False / None (missing) for a boolean feature, whatever pandas stored."""
    if v is None or v is pd.NA or (isinstance(v, float) and np.isnan(v)):
        return None
    return bool(v)


def _on(row: pd.Series, col: str, op: str | None, val: float | None) -> bool:
    v = row.get(col)
    if op is None:
        return bool(_flag(v))
    if v is None or v is pd.NA or pd.isna(v):
        return False
    x = float(v)
    return {">=": x >= val, "<=": x <= val, ">": x > val, "<": x < val, "=": x == val}[op]


def explain_day(f: pd.DataFrame, d: pd.Timestamp, metric: str, dev: float, effects: dict[str, list[dict]],
                illness: bool, p: dict) -> list[dict]:
    """The drivers of one unusual day, most important first."""
    row = f.loc[d]
    drivers: list[dict] = []
    for col, words in QUALITY.items():
        if col in f and _flag(row.get(col)) is False:
            drivers.append({"kind": "quality", "what": words})
    for col, words in CONTEXT.items():
        if col in f and _flag(row.get(col)):
            drivers.append({"kind": "context", "what": words})
    if illness:
        drivers.append({"kind": "context", "what": "the possible-illness pattern (HRV down, resting HR up, no load spike)"})
    used = set()
    for col, items in effects.items():
        if col not in f:
            continue
        for e in items:
            got = _applies(f, d, col, e, p)
            if got is None:
                continue
            effect, what = got
            used.add(col)
            when = {0: "that day", 1: "the day before"}.get(int(e["lag_days"]), f"{int(e['lag_days'])} days before")
            drivers.append({"kind": "effect", "what": f"{what} {when}", "exposure": col, "lag_days": int(e["lag_days"]),
                            "effect": round(effect, 2), "helps_explain": bool(np.sign(effect) == np.sign(dev)),
                            "p_meaningful": float(e["p_meaningful"]), "hypothesis_id": e["hypothesis_id"]})
            break
    prev = d - pd.Timedelta(days=1)
    if prev in f.index:
        for col, words in OTHERS.items():
            if col in used or col not in f:
                continue
            z, _, _ = robust_z(f[col].astype(float), int(p["baseline_days"]), int(p["min_baseline"]))
            zv = z.get(prev)
            if zv is not None and pd.notna(zv) and zv >= p["other_z"]:
                drivers.append({"kind": "unusual", "what": words, "exposure": col, "z": round(float(zv), 1)})
    return drivers


def _applies(f: pd.DataFrame, d: pd.Timestamp, col: str, e: dict, p: dict) -> tuple[float, str] | None:
    """(effect on the outcome, words) when this estimate's exposure was there at its lag."""
    day = d - pd.Timedelta(days=int(e["lag_days"]))
    if day not in f.index:
        return None
    src = f.loc[day]
    if e["kind"] == "dose":
        x = src.get(col)
        if x is None or pd.isna(x):
            return None
        past = f[col].astype(float).loc[:day - pd.Timedelta(days=1)].tail(int(p["baseline_days"])).dropna()
        usual = float(past.median()) if len(past) else 0.0
        spread = float(1.4826 * (past - usual).abs().median()) if len(past) else 0.0
        amount = float(x) - usual
        if amount == 0 or (spread > 0 and abs(amount) < spread):
            return None  # an ordinary dose for me explains nothing
        words = DOSE_WORDS.get(col, lambda v: f"{LABELS.get(col, col.replace('_', ' '))} {v:g}")(float(x))
        return float(e["estimate"]) * amount, words
    if not _on(src, col, e["_op"], e["_val"]):
        return None
    words = BINARY_WORDS.get(col) or describe(Hypothesis(e["hypothesis_id"], e["exposure"], e["outcome"],
                                                         int(e["lag_days"])))[0]
    return float(e["estimate"]), words


def summarize_day(metric: str, d: date, value: float, baseline: float, z: float, drivers: list[dict]) -> tuple[str, float]:
    label, unit, fmt = OUTCOMES[metric]
    way = "high" if value > baseline else "low"
    head = (f"{label[0].upper()}{label[1:]} was unusually {way} on {d:%b %d} ({fmt % value} {unit}; "
            f"usual {fmt % baseline}, {abs(z):.1f} SD)")
    dev = value - baseline
    helping = [x for x in drivers if x["kind"] == "effect" and x["helps_explain"]
               and abs(x["effect"]) >= MINOR * abs(dev)]
    share = min(1.0, max(0.0, sum(x["effect"] for x in helping) / dev)) if dev else 0.0
    bits = []
    q = [x["what"] for x in drivers if x["kind"] == "quality"]
    if q:
        bits.append(f"the reading may be off: {', '.join(q)}")
    c = [x["what"] for x in drivers if x["kind"] == "context"]
    if c:
        bits.append(", ".join(c))
    if helping:
        bits.append("; ".join(f"{x['what']} (your estimate: about {abs(x['effect']):.1f} {unit} "
                              f"{'more' if x['effect'] > 0 else 'less'})" for x in helping))
    o = [x["what"] for x in drivers if x["kind"] == "unusual"]
    if o:
        bits.append(f"also unusual the day before: {', '.join(o)}")
    if not bits:
        return head + ". Nothing in the record explains it.", 0.0
    tail = f" Your own estimates account for about {share * 100:.0f}% of it." if helping else ""
    return head + ". Around it: " + "; ".join(bits) + "." + tail, round(share, 2)


# ------------------------------------------------------------------ from the database
def build_anomalies(conn: psycopg.Connection, end: date, days: int = 30, params: dict | None = None) -> int:
    p = {**DEFAULTS, **(params or {})}
    start = end - timedelta(days=days - 1)
    f = fetch_df(conn, "SELECT * FROM analytics.daily_features WHERE date BETWEEN %s AND %s ORDER BY date",
                 (start - timedelta(days=int(p["baseline_days"]) + 7), end))
    if f.empty:
        return 0
    f = f.set_index(pd.to_datetime(f["date"])).asfreq("D")
    effects = fetch_all(conn, """SELECT DISTINCT ON (e.hypothesis_id) e.hypothesis_id, e.estimate, e.p_meaningful,
                                        h.exposure, h.outcome, h.lag_days, h.kind
                                 FROM analytics.effect_estimate e JOIN analytics.hypothesis h ON h.id = e.hypothesis_id
                                 WHERE e.computed_on <= %s ORDER BY e.hypothesis_id, e.computed_on DESC""", (end,))
    effects = [e for e in effects if not is_sensitive(parse_exposure(e["exposure"])[0])]
    ill = {r["date"] for r in fetch_all(conn, """SELECT date FROM analytics.daily_flag WHERE rule_id = %s AND fired
                                                  AND date BETWEEN %s AND %s""", (ILLNESS_RULE, start, end))}
    rows = []
    for metric in OUTCOMES:
        if metric not in f:
            continue
        s = f[metric].astype(float)
        z, med, sd = robust_z(s, int(p["baseline_days"]), int(p["min_baseline"]))
        eff = credible_effects(effects, metric, p["min_p"])
        for d in s.index[(s.index >= pd.Timestamp(start)) & (z.abs() >= p["min_z"])]:
            drivers = explain_day(f, d, metric, float(s[d] - med[d]), eff, d.date() in ill, p)
            text, share = summarize_day(metric, d.date(), float(s[d]), float(med[d]), float(z[d]), drivers)
            rows.append({"date": d.date(), "metric": metric, "value": round(float(s[d]), 2),
                         "baseline": round(float(med[d]), 2), "z": round(float(z[d]), 2), "drivers": drivers,
                         "explained_share": share, "summary": text, "computed_on": end})
    execute(conn, "DELETE FROM analytics.anomaly WHERE date BETWEEN %s AND %s", (start, end))
    return upsert(conn, "analytics.anomaly", rows, conflict=["date", "metric"]) if rows else 0


def anomaly_candidates(conn: psycopg.Connection, as_of: date) -> list[dict]:
    """Ranking candidates (C3): unusual days of the past week and G3 shifts of the past four weeks."""
    out = []
    for r in fetch_all(conn, "SELECT * FROM analytics.anomaly WHERE date BETWEEN %s AND %s",
                       (as_of - timedelta(days=6), as_of)):
        top = next((x for x in r["drivers"] if x["kind"] == "effect" and x["helps_explain"]), None)
        out.append({"kind": "anomaly", "key": f"anomaly:{r['metric']}:{r['date']}",
                    "exposure": (top or {}).get("exposure", "none"), "outcome": r["metric"],
                    "p": round(float(2 * stats.norm.cdf(abs(float(r["z"]))) - 1), 3),
                    "estimate": float(r["value"]) - float(r["baseline"]), "swc": None, "novelty": 0.5,
                    "text": r["summary"]})
    for r in fetch_all(conn, "SELECT * FROM analytics.change_point WHERE date BETWEEN %s AND %s",
                       (as_of - timedelta(days=27), as_of)):
        log = next((c for c in r["causes"] if c["kind"] == "log"), None)
        col = {v: k for k, v in LOGS.items()}.get((log or {}).get("what"), "none")
        causes = "; ".join(c["what"] for c in r["causes"]) or "nothing logged nearby"
        out.append({"kind": "anomaly", "key": f"change:{r['metric']}:{r['date']}", "exposure": col,
                    "outcome": {"ln_hrv": "hrv", "sleep_midpoint": "sleep_midpoint_min"}.get(r["metric"], r["metric"]),
                    "p": float(r["prob"]), "estimate": float(r["shift"]), "swc": None, "novelty": 0.5,
                    "text": f"{r['summary']}. Around then: {causes}."})
    return out
