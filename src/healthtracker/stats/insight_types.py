"""C4: insight types beyond simple pairwise effects.

  threshold    the sleep duration below which next-morning HRV starts to drop (a hinge model,
               chosen by model evidence over a flat one)
  timing       how much later each hour of later caffeine or dinner moves bedtime
  interaction  whether a hard day costs more recovery after a short night
  progress     aerobic fitness (D3) and sprint and jump tests, each with an interval
  regularity   how sleep regularity (E1) moved over four weeks

Anomalies (E5, G3) and follow-through (G5) come from their own items. Every candidate is a
record whose sentence is rendered by code; the probability is P(|effect| > SWC) (or, for a
trend, P(direction)). Ranking happens in ranking.py.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import psycopg
from scipy import stats

from healthtracker.db import fetch_all
from healthtracker.stats.bayes import VAGUE, design, fit, summarize
from healthtracker.stats.hypothesis import excluded_days
from healthtracker.stats.records import confidence_word

SWC_SD = 0.2


def _hm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60}h{m % 60:02d}"


def threshold(f: pd.DataFrame) -> dict | None:
    """Next-morning HRV vs last night's sleep: is there a duration below which it drops?"""
    f = f.sort_index().reindex(pd.date_range(f.index.min(), f.index.max(), freq="D"))
    df = pd.DataFrame({"Y": f["hrv"].astype(float).shift(-1), "sleep": f["sleep_total_min"].astype(float)})
    df = df[~excluded_days(f, ("sick", "travel", "injury", "implausible"))].dropna()
    if len(df) < 40:
        return None
    X0, y, consecutive, y_sd, _, _ = design(df[["Y"]], [])
    flat = fit(X0, y, consecutive, np.zeros(1), np.full(1, VAGUE))
    best = None
    for t in range(360, 481, 15):
        d = df.assign(short=(t - df["sleep"]).clip(lower=0) / 60.0)
        if (d["short"] > 0).sum() < 8 or (d["short"] == 0).sum() < 8:
            continue
        X, y2, c2, ysd2, sc, _ = design(d[["Y", "short"]], [("short", True)])
        post = fit(X, y2, c2, np.zeros(X.shape[1]), np.r_[0.25, VAGUE])
        if post and (best is None or post["log_ev"] > best[1]["log_ev"]):
            best = (t, post, ysd2 / sc[0])
    if not best or flat is None or best[1]["log_ev"] - flat["log_ev"] < np.log(3):  # evidence ratio over 3
        return None
    t, post, to_raw = best
    s = summarize(post, 0, to_raw, SWC_SD)
    if s["estimate"] >= 0:
        return None
    word = confidence_word(s["p_meaningful"])
    return {"kind": "threshold", "key": "threshold:sleep->hrv", "exposure": "sleep_total_min", "outcome": "hrv",
            "p": s["p_meaningful"], "estimate": s["estimate"], "swc": s["swc"], "threshold": t,
            "text": f"{word.capitalize()}: below about {_hm(t)} of sleep, next-morning HRV drops by about "
                    f"{abs(s['estimate']):.0f} ms per hour short (80% interval {abs(s['hi80']):.0f} to "
                    f"{abs(s['lo80']):.0f} ms)."}


def timing(f: pd.DataFrame) -> list[dict]:
    """How much later bedtime runs for each hour later of the last caffeine or dinner."""
    out = []
    f = f.sort_index().reindex(pd.date_range(f.index.min(), f.index.max(), freq="D"))
    for col, word in (("last_caffeine_hour", "caffeine"), ("last_meal_hour", "dinner")):
        if col not in f:
            continue
        df = pd.DataFrame({"Y": f["bedtime_hour"].astype(float).shift(-1) * 60, "x": f[col].astype(float)})
        df = df[~excluded_days(f, ("sick", "travel", "implausible"))].dropna()
        if len(df) < 30 or df["x"].std() == 0:
            continue
        X, y, c, y_sd, sc, _ = design(df, [("x", True)])
        post = fit(X, y, c, np.zeros(X.shape[1]), np.r_[0.25, VAGUE])
        if post is None:
            continue
        s = summarize(post, 0, y_sd / sc[0], SWC_SD)
        conf = confidence_word(s["p_meaningful"])
        out.append({"kind": "timing", "key": f"timing:{col}->bedtime", "exposure": col, "outcome": "bedtime_hour",
                    "p": s["p_meaningful"], "estimate": s["estimate"], "swc": s["swc"],
                    "text": f"{conf.capitalize()}: each hour later {word} goes with bedtime about "
                            f"{abs(s['estimate']):.0f} min {'later' if s['estimate'] > 0 else 'earlier'} (80% interval "
                            f"{s['lo80']:.0f} to {s['hi80']:.0f} min)."})
    return out


def interaction(f: pd.DataFrame) -> dict | None:
    """Does a hard day cost more next-morning HRV after a short night?"""
    f = f.sort_index().reindex(pd.date_range(f.index.min(), f.index.max(), freq="D"))
    hard = (f["load_z_28d"].astype(float) >= 1.0).astype(float)
    short = (f["sleep_total_min"].astype(float) < 420).astype(float)
    df = pd.DataFrame({"Y": f["hrv"].astype(float).shift(-1), "hard": hard, "short": short,
                       "both": hard * short})
    df = df[~excluded_days(f, ("sick", "travel", "injury", "implausible"))].dropna()
    if len(df) < 40 or df["both"].sum() < 4:
        return None
    X, y, c, y_sd, _, _ = design(df, [("both", False), ("hard", False), ("short", False)])
    post = fit(X, y, c, np.zeros(X.shape[1]), np.r_[0.25, 0.25, 0.25, VAGUE])
    if post is None:
        return None
    s = summarize(post, 0, y_sd, SWC_SD)
    conf = confidence_word(s["p_meaningful"])
    return {"kind": "interaction", "key": "interaction:hard*short->hrv", "exposure": "hard_day x short_sleep",
            "outcome": "hrv", "p": s["p_meaningful"], "estimate": s["estimate"], "swc": s["swc"],
            "text": f"{conf.capitalize()}: a hard day after a short night costs about {abs(s['estimate']):.0f} ms "
                    f"{'more' if s['estimate'] < 0 else 'less'} next-morning HRV than the two would add up to "
                    f"(80% interval {s['lo80']:.0f} to {s['hi80']:.0f} ms; {int(df['both'].sum())} such days)."}


def progress(conn: psycopg.Connection, d: date) -> list[dict]:
    out = []
    t = fetch_all(conn, """SELECT * FROM analytics.fitness_trend WHERE metric = 'aerobic_pace' AND date <= %s
                           ORDER BY date DESC LIMIT 1""", (d,))
    if t and t[0]["slope_week"] is not None and t[0]["n_obs"] >= 10:
        r = t[0]
        se = (r["slope_hi80"] - r["slope_lo80"]) / (2 * 1.2816) or 1.0
        p = float(stats.norm.cdf(abs(r["slope_week"]) / se))
        better = r["slope_week"] < 0
        out.append({"kind": "progress", "key": "progress:aerobic_pace", "exposure": "training", "outcome": "aerobic_pace",
                    "p": round(p, 3), "estimate": r["slope_week"], "swc": None,
                    "text": f"{confidence_word(p).capitalize()}: aerobic fitness is "
                            f"{'improving' if better else 'slipping'}: pace at your reference HR changes about "
                            f"{abs(r['slope_week']):.1f} s/km a week (80% interval {r['slope_lo80']:.1f} to "
                            f"{r['slope_hi80']:.1f})."})
    tests = fetch_all(conn, """SELECT test, date, value FROM core.performance_test
                               WHERE test IN ('sprint_10m', 'sprint_30m', 'cmj', 'broad_jump') AND date <= %s
                               ORDER BY test, date""", (d,))
    by: dict[str, list] = {}
    for r in tests:
        by.setdefault(r["test"], []).append(r)
    for test, rows in by.items():
        if len(rows) < 3:
            continue
        x = np.array([(r["date"] - rows[0]["date"]).days / 30.0 for r in rows])
        y = np.array([float(r["value"]) for r in rows])
        res = stats.linregress(x, y)
        if not np.isfinite(res.stderr) or res.stderr == 0:
            continue
        lower_better = test.startswith("sprint")
        good = (res.slope < 0) if lower_better else (res.slope > 0)
        p = float(stats.t.cdf(abs(res.slope) / res.stderr, len(rows) - 2))
        unit = "s" if lower_better else "cm"
        out.append({"kind": "progress", "key": f"progress:{test}", "exposure": "training", "outcome": test,
                    "p": round(p, 3), "estimate": round(res.slope, 3), "swc": None,
                    "text": f"{confidence_word(p).capitalize()}: {test.replace('_', ' ')} is "
                            f"{'improving' if good else 'not improving'}: about {abs(res.slope):.2f} {unit} a month "
                            f"over {len(rows)} tests."})
    return out


def regularity(f: pd.DataFrame) -> dict | None:
    s = f["sri_28d"].astype(float).dropna() if "sri_28d" in f else pd.Series(dtype=float)
    if len(s) < 29:
        return None
    now, then = float(s.iloc[-1]), float(s.iloc[-29])
    change = now - then
    if abs(change) < 5:
        return None
    return {"kind": "regularity", "key": "regularity:sri", "exposure": "sleep timing", "outcome": "sri_28d",
            "p": 0.8 if abs(change) >= 10 else 0.6, "estimate": round(change, 1), "swc": 5.0,
            "text": f"Sleep regularity {'rose' if change > 0 else 'fell'} from {then:.0f} to {now:.0f} over four weeks "
                    f"(100 means the same sleep times every day)."}


def candidates(conn: psycopg.Connection, f: pd.DataFrame, d: date) -> list[dict]:
    out = [x for x in (threshold(f), interaction(f), regularity(f)) if x]
    out += timing(f) + progress(conn, d)
    return out
