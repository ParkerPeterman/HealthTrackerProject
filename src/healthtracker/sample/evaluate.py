"""Section 13: the demo athlete as a test bench, at 14, 30, 60, and 120 days of data.

For each length a fresh scratch database gets a demo athlete (generator.py, the same seed, the
same end date) and a full backfill, then the methods are read back against what was planted:

  * B1 effects: P(|effect| > SWC) and the estimate for each planted effect (should rise with
    days and keep the planted sign), and for exposures with nothing planted (should stay low)
  * B4: the lag profile of a game on HRV (planted: -12% next morning, -5% the morning after)
  * C4: the hard-day-after-short-night interaction (planted: another -10%)
  * D3: the trend in pace at the reference HR (planted: getting fitter) and the temperature
    coefficient (planted heat effect on HR)
  * G2: which forecasts beat naive in their backtests
  * G3: shifts found (planted: bedtime 45 min later from 72% of the way; the overreaching
    block also moves HRV)
  * G4: unusual days and how many the record explains
  * H3: the medication analysis (nothing planted: should find nothing)
  * runtime: the backfill, then a nightly and a weekly run on the last day, against the
    2- and 10-minute budgets

Nothing here touches the real database: each run uses `<demo database>_eval_<days>` and drops
it afterwards unless asked to keep it. The demo home point is set for the run so sessions get
the synthetic weather.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import date

from healthtracker.config import get_settings
from healthtracker.db import connect, fetch_all, fetch_one, use_database

END = date(2026, 6, 30)
PLANTED = [  # label, exposure, outcome, lag, kind, planted sign
    ("caffeine from 14:00 -> sleep score", "last_caffeine_hour >= 14", "sleep_score", 1, "binary", -1),
    ("caffeine from 14:00 -> sleep", "last_caffeine_hour >= 14", "sleep_total_min", 1, "binary", -1),
    ("each drink -> HRV", "alcohol_drinks", "hrv", 1, "dose", -1),
    ("each drink -> resting HR", "alcohol_drinks", "resting_hr", 1, "dose", 1),
    ("late meal -> sleep score", "last_meal_hour >= 21", "sleep_score", 1, "binary", -1),
    ("game -> HRV next morning", "had_game", "hrv", 1, "binary", -1),
    ("short night -> energy", "sleep_total_min < 390", "energy", 0, "binary", -1),
]
NOT_PLANTED = [
    ("fiber 30 g+ -> sleep score", "fiber_g >= 30", "sleep_score", 1, "binary"),
    ("low protein -> soreness", "protein_g_per_kg < 1.4", "soreness", 1, "binary"),
    ("low carbs -> energy", "carbs_g < 200", "energy", 1, "binary"),
    ("under 2000 kcal -> energy", "kcal < 2000", "energy", 1, "binary"),
]
DEMO_HOME = ("41.88", "-87.63")


@dataclass
class Result:
    days: int
    effects: list[dict] = field(default_factory=list)
    lagged: dict | None = None
    interaction: dict | None = None
    fitness: dict = field(default_factory=dict)
    forecasts: list[dict] = field(default_factory=list)
    change_points: list[dict] = field(default_factory=list)
    anomalies: dict = field(default_factory=dict)
    medication: dict = field(default_factory=dict)
    runtime: dict = field(default_factory=dict)


def _eval_url(days: int) -> str:
    base = get_settings().demo_database_url
    head, name = base.rsplit("/", 1)
    return f"{head}/{name.split('?')[0]}_eval_{days}"


def evaluate_at(days: int, seed: int = 7, keep: bool = False, runtime: bool = True) -> Result:
    from healthtracker.migrate import drop_database, ensure_database, migrate
    from healthtracker.pipeline.jobs import backfill, nightly, weekly
    from healthtracker.sample.generator import generate

    os.environ.setdefault("HOME_LAT", DEMO_HOME[0])
    os.environ.setdefault("HOME_LON", DEMO_HOME[1])
    get_settings.cache_clear()
    url = _eval_url(days)
    if url == get_settings().database_url:
        raise ValueError("the evaluation database must not be the real one")
    drop_database(url)
    ensure_database(url)
    migrate(url)
    use_database(url)
    out = Result(days)
    try:
        with connect() as conn:
            info = generate(conn, days=days, end=END, seed=seed)
        t0 = time.monotonic()
        rep = backfill(info["start"], info["end"], llm_days=0)
        out.runtime["backfill_s"] = round(time.monotonic() - t0)
        out.runtime["backfill_ok"] = rep.ok
        if runtime:
            n = nightly(END, skip_ingest=True, use_llm=False)
            w = weekly(END)
            out.runtime.update(nightly_s=round(n.elapsed_s), weekly_s=round(w.elapsed_s), nightly_ok=n.ok,
                               weekly_ok=w.ok)
        with connect() as conn:
            collect(conn, out)
    finally:
        if not keep:
            use_database(get_settings().database_url)
            drop_database(url)
    return out


def collect(conn, out: Result) -> None:
    from healthtracker.stats.insight_types import interaction
    from healthtracker.stats.insights import load_features_frame

    est = {(r["exposure"], r["outcome"], r["lag_days"], r["kind"]): r for r in fetch_all(conn, """
        SELECT DISTINCT ON (e.hypothesis_id) e.*, h.exposure, h.outcome, h.lag_days, h.kind
        FROM analytics.effect_estimate e JOIN analytics.hypothesis h ON h.id = e.hypothesis_id
        ORDER BY e.hypothesis_id, e.computed_on DESC""")}
    for label, exp, outc, lag, kind, sign in PLANTED:
        r = est.get((exp, outc, lag, kind))
        out.effects.append({"label": label, "planted": True, **_effect(r, sign)})
    for label, exp, outc, lag, kind in NOT_PLANTED:
        r = est.get((exp, outc, lag, kind))
        out.effects.append({"label": label, "planted": False, **_effect(r, None)})
    lag = est.get(("had_game", "hrv", 1, "lagged"))
    if lag and lag["lag_profile"]:
        lp = lag["lag_profile"]
        out.lagged = {"tau": lp["tau"], "shape_clear": lp["shape_clear"],
                      "lags": [(x["lag"], x["estimate"]) for x in lp["lags"]]}
    f = load_features_frame(conn, END)
    if not f.empty:
        it = interaction(f)
        out.interaction = {k: it[k] for k in ("estimate", "p")} if it else None
    t = fetch_one(conn, """SELECT * FROM analytics.fitness_trend WHERE metric = 'aerobic_pace' AND date <= %s
                           ORDER BY date DESC LIMIT 1""", (END,))
    m = fetch_one(conn, """SELECT covariates FROM analytics.fitness_model WHERE metric = 'aerobic_pace'
                           ORDER BY computed_on DESC LIMIT 1""")
    out.fitness = {"slope_week": t and t["slope_week"], "slope_hi80": t and t["slope_hi80"], "n_runs": t and t["n_obs"],
                   "temp_per_c": ((m or {}).get("covariates") or {}).get("per_unit", {}).get("temp"),
                   "humid_per_c": ((m or {}).get("covariates") or {}).get("per_unit", {}).get("humid")}
    out.forecasts = [{"metric": r["metric"], "model": r["model"], "beats_naive": r["beats_naive"],
                      "backtest_n": r["backtest_n"], "mae_model": r["mae_model"], "mae_naive": r["mae_naive"]}
                     for r in fetch_all(conn, """SELECT DISTINCT ON (metric, model) * FROM analytics.forecast
                                                 ORDER BY metric, model, computed_on DESC""")]
    out.change_points = [{"metric": r["metric"], "date": r["date"], "shift": float(r["shift"]), "prob": float(r["prob"])}
                         for r in fetch_all(conn, "SELECT * FROM analytics.change_point ORDER BY date")]
    a = fetch_one(conn, """SELECT count(*) AS n, count(*) FILTER (WHERE summary NOT LIKE '%%Nothing in the record%%')
                                  AS explained, round(avg(explained_share)::numeric, 2) AS share
                           FROM analytics.anomaly""")
    out.anomalies = dict(a or {})
    med = fetch_one(conn, """SELECT count(*) AS n, max(p_meaningful) AS max_p FROM analytics.medication_effect
                             WHERE computed_on = (SELECT max(computed_on) FROM analytics.medication_effect)""")
    out.medication = dict(med or {})


def _effect(r: dict | None, sign: int | None) -> dict:
    if not r or r["estimate"] is None:
        return {"estimate": None, "p": None, "days": r["n_days"] if r else 0, "sign_ok": None}
    est = float(r["estimate"])
    return {"estimate": round(est, 2), "p": float(r["p_meaningful"]) if r["p_meaningful"] is not None else None,
            "days": r["n_days"], "sign_ok": None if sign is None else (est * sign > 0)}


# ------------------------------------------------------------------ the report
def render(results: list[Result]) -> str:
    days = [r.days for r in results]
    head = "| | " + " | ".join(f"{d} days" for d in days) + " |"
    sep = "|---|" + "---|" * len(days)
    lines = ["# Demo athlete at 14, 30, 60, and 120 days", "",
             "P = P(|effect| > SWC); a planted effect should rise and keep its sign, the others stay low.", "",
             head, sep]
    labels = [e["label"] for e in results[0].effects]
    for i, label in enumerate(labels):
        cells = []
        for r in results:
            e = r.effects[i]
            if e["p"] is None:
                cells.append("too early")
            else:
                mark = "" if e["sign_ok"] is None else (" ✓" if e["sign_ok"] else " ✗ sign")
                cells.append(f"P {e['p']:.2f} ({e['estimate']:+g}){mark}")
        lines.append(f"| {label}{' (planted)' if results[0].effects[i]['planted'] else ''} | " + " | ".join(cells) + " |")

    def row(name: str, fn) -> None:
        lines.append(f"| {name} | " + " | ".join(fn(r) for r in results) + " |")

    row("B4 game -> HRV lags", lambda r: "–" if not r.lagged else
        f"tau {r.lagged['tau']:g}{'' if r.lagged['shape_clear'] else ' (unclear)'}: "
        + ", ".join(f"{e:+.1f}" for _, e in r.lagged["lags"]))
    row("C4 hard day after short night", lambda r: "too few days" if not r.interaction else
        f"{r.interaction['estimate']:+.1f} ms (P {r.interaction['p']:.2f})")
    row("D3 pace trend, s/km a week", lambda r: "–" if r.fitness.get("slope_week") is None else
        f"{float(r.fitness['slope_week']):+.2f} (hi80 {float(r.fitness['slope_hi80']):+.2f})")
    row("D3 temp / humid, s/km per °C", lambda r: "–" if r.fitness.get("temp_per_c") is None else
        f"{r.fitness['temp_per_c']:+.2f} / {r.fitness.get('humid_per_c') or 0:+.2f}")
    row("G2 beats naive", lambda r: ", ".join(f"{f['metric']}{'' if f['model'] == 'local_linear_trend' else ' (D2)'}"
                                              for f in r.forecasts if f["beats_naive"]) or "none")
    row("G3 shifts", lambda r: "; ".join(f"{c['metric']} {c['date']:%b %d} {c['shift']:+.1f}" for c in r.change_points)
        or "none")
    row("G4 unusual days (explained)", lambda r: f"{r.anomalies.get('n', 0)} ({r.anomalies.get('explained', 0)})")
    row("H3 medication (max P)", lambda r: "none" if not r.medication.get("n") else
        f"{r.medication['n']} estimates, max P {float(r.medication['max_p'] or 0):.2f}")
    row("runtime: backfill / nightly / weekly", lambda r: f"{r.runtime.get('backfill_s')} s / "
        f"{r.runtime.get('nightly_s', '–')} s / {r.runtime.get('weekly_s', '–')} s")
    lines += ["", "Budgets: nightly about 2 minutes, weekly about 10 (brief section 13)."]
    return "\n".join(lines)
