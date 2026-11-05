"""H3: what missed doses and dose timing go along with. Weekly, shadow for the first month.

Sensitive throughout: the medication columns are joined to a private copy of the daily
features in memory, never written to daily_features, and results go to
analytics.medication_effect and the approved templates 6-8 only.

  * missed doses (recorded misses vs recorded doses; unknown days left out) on HRV, resting
    HR, sleep duration, mood, and energy, the same day and the two after
  * dose time (hours after midnight) against that night's sleep and my sleep rating
  * travel and alcohol enter as controls; when nearly all misses were on travel days the
    finding is "can't be separated from travel"
  * priors are weak and centred on no effect, Normal(0, 0.5 SD), because the evidence on
    day-to-day effects is mixed; with good adherence the honest answer stays "too early to
    tell" for a long time
  * a missed-dose finding needs at least `min_missed_for_finding` (8) missed days in its
    estimate: the Section 13 evaluation found a spurious "likely" from two missed days that
    happened to follow a trip

Nothing here is advice: a timing finding only ever says it may be worth asking the prescriber.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import psycopg

from healthtracker.components import component_status, is_live
from healthtracker.db import execute, fetch_all, fetch_df, insert, upsert
from healthtracker.medication.messages import med_params, render
from healthtracker.stats.bayes import estimate
from healthtracker.stats.bayes import swc_params as load_swc
from healthtracker.stats.hypothesis import UNITS, Hypothesis, build_frame
from healthtracker.stats.records import confidence_word

PRIOR = (0.0, 0.5, "weak prior centred on no effect: evidence on day-to-day medication effects is mixed")
EXCLUDES = ("sick", "injury", "implausible")  # travel is a control here, not an exclusion
MISSED_OUTCOMES = (("hrv", (1, 2)), ("resting_hr", (1, 2)), ("sleep_total_min", (1,)), ("mood", (0, 1)),
                   ("energy", (0, 1)))
DOSE_TIME_OUTCOMES = (("sleep_total_min", 1), ("sleep_quality", 1))
WORDS = {"hrv": "HRV", "resting_hr": "resting HR", "sleep_total_min": "sleep", "mood": "mood", "energy": "energy",
         "sleep_quality": "your sleep rating"}


def private_frame(features: pd.DataFrame, med: pd.DataFrame) -> pd.DataFrame:
    """A copy of the features with the medication columns; it lives only in memory."""
    f = features.copy()
    m = med.set_index(pd.to_datetime(med["date"]))
    f["med_missed"] = m["med_status"].map({"missed": 1.0, "taken": 0.0}).reindex(f.index)
    f["med_dose_time"] = m["dose_time_local"].astype(float).reindex(f.index)
    return f


def with_controls(df: pd.DataFrame, f: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col, name in (("is_travel", "ctl_travel"), ("tag_alcohol", "ctl_alcohol")):
        if col in f:
            df[name] = f[col].reindex(df.index).astype("boolean").fillna(False).astype(float)
    return df


def travel_share(f: pd.DataFrame) -> tuple[float | None, int]:
    missed = f["med_missed"] == 1
    n = int(missed.sum())
    if not n or "is_travel" not in f:
        return None, n
    return round(float(f.loc[missed, "is_travel"].astype("boolean").fillna(False).mean()), 3), n


def effect_words(outcome: str, est: float, per: str = "") -> str:
    unit = UNITS.get(outcome, "points")
    return f"{abs(est):.0f} {unit} {'higher' if est > 0 else 'lower'}{per}"


def analyze(features: pd.DataFrame, med: pd.DataFrame, swc: dict[str, float]) -> tuple[list[dict], float | None, int]:
    f = private_frame(features, med)
    share, n_missed = travel_share(f)
    rows = []
    for outcome, lags in MISSED_OUTCOMES:
        for lag in lags:
            h = Hypothesis(0, "med_missed", outcome, lag, excludes=EXCLUDES)
            df = with_controls(build_frame(f, h)[0], f)
            e = estimate(df, h, PRIOR, swc.get(outcome, swc.get("default", 0.2)))
            rows.append({"exposure": "med_missed", "outcome": outcome, "lag_days": lag, "n_days": e.n_days,
                         "n_exposed": int(df["E"].sum()) if len(df) else 0, **_nums(e)})
    for outcome, lag in DOSE_TIME_OUTCOMES:
        if outcome not in f:
            continue
        h = Hypothesis(0, "med_dose_time", outcome, lag, excludes=EXCLUDES, kind="dose")
        df = with_controls(build_frame(f, h)[0], f)
        e = estimate(df, h, PRIOR, swc.get(outcome, swc.get("default", 0.2)))
        rows.append({"exposure": "med_dose_time", "outcome": outcome, "lag_days": lag, "n_days": e.n_days,
                     "n_exposed": None, **_nums(e)})
    return rows, share, n_missed


def _nums(e) -> dict:
    return {k: getattr(e, k) for k in ("estimate", "lo80", "hi80", "lo95", "hi95", "p_meaningful")}


def findings(rows: list[dict], share: float | None, n_missed: int, name: str, p: dict) -> list[tuple[str, dict]]:
    """Which of templates 6-8 apply, with their values. At most one missed-dose line and one timing line."""
    out: list[tuple[str, dict]] = []
    if n_missed >= p.get("min_missed_days", 3) and share is not None and share >= p.get("travel_share_confounded", 0.7):
        out.append(("travel_confounded", {}))
    else:
        # A finding needs enough missed days behind it: with two or three, one odd stretch (the days
        # after a trip, say) is the whole estimate. Below that the answer stays "too early to tell".
        need = p.get("min_missed_for_finding", 8)
        missed = [r for r in rows if r["exposure"] == "med_missed" and r["p_meaningful"] is not None
                  and (r.get("n_exposed") or 0) >= need]
        best = max(missed, key=lambda r: r["p_meaningful"], default=None)
        if best and best["p_meaningful"] >= p.get("finding_min_p", 0.6):
            out.append(("missed_effect", {"outcome": WORDS[best["outcome"]],
                                          "effect": effect_words(best["outcome"], best["estimate"]),
                                          "confidence": confidence_word(best["p_meaningful"])}))
        else:
            out.append(("missed_effect_early", {"outcome": "HRV, resting HR, sleep, or mood"}))
    timing = [r for r in rows if r["exposure"] == "med_dose_time" and r["p_meaningful"] is not None
              and r["p_meaningful"] >= p.get("finding_min_p", 0.6)]
    if timing:
        t = max(timing, key=lambda r: r["p_meaningful"])
        out.append(("dose_time", {"name": name, "outcome": WORDS[t["outcome"]],
                                  "effect": effect_words(t["outcome"], t["estimate"], " per hour later"),
                                  "confidence": confidence_word(t["p_meaningful"])}))
    return out


def run_med_analysis(conn: psycopg.Connection, as_of: date) -> int:
    from healthtracker.stats.insights import load_features_frame

    meds = fetch_all(conn, "SELECT * FROM core.medication WHERE active AND start_date <= %s ORDER BY id", (as_of,))
    if not meds:
        return 0
    features = load_features_frame(conn, as_of)
    if features.empty:
        return 0
    swc, p = load_swc(conn), med_params(conn)
    live = is_live(component_status(conn), "COMP_MED_ANALYSIS")
    n = 0
    for m in meds:
        med = fetch_df(conn, """SELECT date, med_status, dose_time_local FROM analytics.medication_daily
                                WHERE medication_id = %s AND date <= %s""", (m["id"], as_of))
        if med.empty:
            continue
        rows, share, n_missed = analyze(features, med, swc)
        upsert(conn, "analytics.medication_effect",
               [{"computed_on": as_of, "medication_id": m["id"], "travel_share": share, "prior_source": PRIOR[2],
                 **{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items()}} for r in rows],
               conflict=["computed_on", "medication_id", "exposure", "outcome", "lag_days"])
        execute(conn, """DELETE FROM analytics.medication_message WHERE date = %s AND medication_id = %s
                         AND template = ANY(%s)""",
                (as_of, m["id"], ["dose_time", "missed_effect", "missed_effect_early", "travel_confounded"]))
        for template, params in findings(rows, share, n_missed, m["name"], p):
            insert(conn, "analytics.medication_message", {
                "date": as_of, "medication_id": m["id"], "template": template, "params": params,
                "text": render(template, **params), "visible": live}, returning=None)
        n += len(rows)
    return n
