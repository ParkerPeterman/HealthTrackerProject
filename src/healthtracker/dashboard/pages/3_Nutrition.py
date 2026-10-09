"""Nutrition: calories and macros vs targets, protein per kg, micronutrient gaps, timing."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from healthtracker.dashboard import common as c
from healthtracker.units import fmt_hour

c.setup("Nutrition")
st.title("Nutrition")
days = c.range_filter(default="30 days")
f = c.features(days)
if f.empty:
    c.empty_state()
    st.stop()
start, end = c.date_window(days)
targets = c.query("SELECT * FROM core.nutrient_target WHERE active")
logged = f[f["food_log_complete"].fillna(False).astype(bool)]
st.caption(f"{len(logged)} of {len(f)} days marked complete. Incomplete days are left out, never counted as zero.")


def target(nutrient: str, basis: str = "per_day") -> tuple[float | None, float | None]:
    t = targets[(targets["nutrient"] == nutrient) & (targets["basis"] == basis)]
    if t.empty:
        return None, None
    r = t.iloc[0]
    return (None if pd.isna(r["min_value"]) else r["min_value"]), (None if pd.isna(r["max_value"]) else r["max_value"])


left, right = st.columns(2, gap="large")
with left:
    fig = go.Figure()
    lo, hi = target("kcal")
    c.band(fig, lo, hi, "goal")
    c.bars(fig, logged["date"], logged["kcal"], "kcal", slot=0)
    c.line(fig, logged["date"], logged["kcal"].rolling(7, min_periods=3).mean(), "7-day avg", slot=1, fmt=".0f")
    c.show(c.base_layout(fig, "Calories", "kcal"), logged[["date", "kcal"]], key="kcal")
with right:
    fig = go.Figure()
    lo, hi = target("protein_g", "per_kg_bodyweight")
    c.band(fig, lo, hi, "target")
    c.line(fig, logged["date"], logged["protein_g_per_kg"], "protein g/kg", slot=0, mode="lines+markers", fmt=".2f")
    c.show(c.base_layout(fig, "Protein per kg body weight", "g/kg", legend=False),
           logged[["date", "protein_g", "protein_g_per_kg"]], key="protein")

macros = logged[["date", "protein_g", "carbs_g", "fat_g"]].copy()
fig = go.Figure()
for i, (col, name) in enumerate((("protein_g", "protein"), ("carbs_g", "carbs"), ("fat_g", "fat"))):
    c.line(fig, macros["date"], macros[col], name, slot=i, mode="lines+markers", fmt=".0f")
c.show(c.base_layout(fig, "Macros", "grams", height=280), macros, key="macros")

st.subheader("Weekly micronutrients vs targets")
micro_t = targets[(targets["evaluation"] == "weekly_avg") & (targets["basis"] == "per_day")]
if micro_t.empty:
    c.empty_state("No weekly nutrient targets. Load defaults in the app (Settings -> Nutrition targets).")
else:
    since = end - pd.Timedelta(days=6)
    nd = c.query("""WITH e AS (
                      SELECT n.date, n.servings, c.fiber_g, c.sodium_mg, c.micros
                      FROM core.food_entry n JOIN core.food_catalog c ON c.id = n.catalog_id
                      JOIN core.food_day_status s ON s.date = n.date AND s.complete
                      WHERE n.date BETWEEN %(s)s AND %(e)s)
                    SELECT 'fiber_g' AS nutrient, sum(servings * coalesce(fiber_g, 0)) AS amount,
                           count(DISTINCT date) AS days FROM e
                    UNION ALL SELECT 'sodium_mg', sum(servings * coalesce(sodium_mg, 0)), count(DISTINCT date) FROM e
                    UNION ALL SELECT m.key, sum(e.servings * m.value::numeric), NULL
                    FROM e, jsonb_each_text(coalesce(e.micros, '{}'::jsonb)) m
                    WHERE m.value ~ '^[0-9.]+$' GROUP BY m.key""", {"s": since, "e": end})
    ndays = int(nd["days"].dropna().max()) if not nd.empty and nd["days"].notna().any() else 0
    rows = []
    for t in micro_t.itertuples():
        amt = nd[nd["nutrient"] == t.nutrient]["amount"].sum() if not nd.empty else 0
        avg = amt / ndays if ndays else None
        goal = t.min_value if not pd.isna(t.min_value) else t.max_value
        pct = None if avg is None or not goal else round(100 * avg / goal)
        rows.append({"nutrient": t.nutrient, "7-day avg": None if avg is None else round(avg, 1),
                     "target": goal, "kind": "min" if not pd.isna(t.min_value) else "max", "% of target": pct})
    tab = pd.DataFrame(rows)
    if ndays == 0:
        c.empty_state("No complete food days in the last week.")
    else:
        status_col = []
        for r in tab.to_dict("records"):
            pct = r["% of target"]
            ok = pct is not None and ((r["kind"] == "min" and pct >= 100) or (r["kind"] == "max" and pct <= 100))
            status_col.append(c.STATUS["good"] if ok else c.STATUS["warning"])
        fig = go.Figure(go.Bar(x=tab["% of target"], y=tab["nutrient"].str.replace("_", " "), orientation="h",
                               marker={"color": status_col, "cornerradius": 4},
                               text=[f"{p}%" + (" ✓" if col == c.STATUS['good'] else " !") for p, col
                                     in zip(tab["% of target"], status_col, strict=False)],
                               textposition="outside", hovertemplate="%{y}: %{x}% of target<extra></extra>"))
        fig.add_vline(x=100, line_width=1, line_color=c.palette()["axis"])
        fig = c.base_layout(fig, f"Last 7 days ({ndays} complete days): % of target", legend=False, height=300)
        fig.update_layout(hovermode="closest")
        c.show(fig, tab, key="micro")
        st.caption("✓ meets target · ! below a minimum or above a maximum. Foods without micronutrient data "
                   "count as zero, so gaps can be data gaps.")

st.subheader("Meal and caffeine timing")
fig = go.Figure()
c.line(fig, logged["date"], logged["last_meal_hour"], "last meal", slot=0, mode="markers", fmt=".1f")
c.line(fig, logged["date"], logged["last_caffeine_hour"], "last caffeine", slot=1, mode="markers", fmt=".1f")
cutoff = c.query("""SELECT value FROM analytics.rule_parameter WHERE rule_id = 'SLEEP_CAFFEINE_CUTOFF'
                    AND name = 'cutoff_hour'""")
if not cutoff.empty:
    h = float(cutoff.iloc[0]["value"])
    c.band(fig, h, h, f"caffeine cutoff {fmt_hour(h)}")
fig = c.base_layout(fig, "Clock time (24h)", "hour")
fig.update_yaxes(tickvals=list(range(6, 25, 3)), ticktext=[fmt_hour(v) for v in range(6, 25, 3)])
c.show(fig, logged[["date", "last_meal_hour", "last_caffeine_hour", "caffeine_mg", "alcohol_drinks"]], key="timing")
