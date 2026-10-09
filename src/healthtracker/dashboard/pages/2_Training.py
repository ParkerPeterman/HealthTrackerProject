"""Training: load (acute vs chronic), run performance, lifting progress, volume, plan vs actual."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from healthtracker.dashboard import common as c
from healthtracker.units import pace_s_per_km_to_s_per_mi

c.setup("Training")
st.title("Training")
days = c.range_filter()
f = c.features(days)
if f.empty:
    c.empty_state()
    st.stop()
d = c.display()
pal = c.palette()
start, end = c.date_window(days)

left, right = st.columns(2, gap="large")
with left:
    fig = go.Figure()
    colors = [pal["series"][1] if g else pal["series"][0] for g in f["had_game"].fillna(False)]
    c.bars(fig, f["date"], f["session_load"], "session load", colors=colors)
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", name="game day",
                             marker={"color": pal["series"][1], "size": 10, "symbol": "square"}))
    fig.data[0].name = "training day"
    c.show(c.base_layout(fig, "Daily load (RPE x minutes)", "load"),
           f[["date", "session_load", "had_game", "n_workouts"]], key="daily")
with right:
    fig = go.Figure()
    c.line(fig, f["date"], f["acute_load_7d"], "acute (7-day total)", slot=0, fmt=".0f")
    c.line(fig, f["date"], f["chronic_load_28d"], "chronic (28-day weekly avg)", slot=1, fmt=".0f")
    c.show(c.base_layout(fig, "Acute vs chronic load", "load"),
           f[["date", "acute_load_7d", "chronic_load_28d", "acwr"]], key="acwr")

left, right = st.columns(2, gap="large")
with left:
    fig = go.Figure()
    c.band(fig, 0.8, 1.3, "0.8-1.3")
    c.line(fig, f["date"], f["acwr"], "ACWR", slot=0, fmt=".2f")
    c.show(c.base_layout(fig, "Acute:chronic workload ratio", "ratio", legend=False), key="ratio")
with right:
    pace = f["pace_at_ref_hr_s_km"]
    if d.imperial:
        pace = pace.map(lambda v: None if pd.isna(v) else pace_s_per_km_to_s_per_mi(v))
    pace_min = pace / 60
    labels = [None if pd.isna(v) else f"{int(v // 60)}:{int(round(v % 60)):02d}" for v in pace]
    fig = go.Figure()
    color = c.palette()["series"][0]
    fig.add_trace(go.Scatter(x=f["date"], y=pace, mode="lines", customdata=labels, name="pace",
                             line={"color": color, "width": 2},
                             hovertemplate=f"pace: %{{customdata}}{d.pace_unit}<extra></extra>"))
    fig = c.base_layout(fig, "Pace at reference HR (lower is fitter)", f"min{d.pace_unit}", legend=False)
    if pace.notna().any():
        lo, hi = int(pace.min() // 15 * 15), int(pace.max() // 15 * 15 + 15)
        ticks = list(range(lo, hi + 1, 15))
        fig.update_yaxes(autorange="reversed", tickvals=ticks, ticktext=[f"{t // 60}:{t % 60:02d}" for t in ticks])
    ref = c.profile().get("ref_hr_bpm")
    c.show(fig, pd.DataFrame({"date": f["date"], f"pace min{d.pace_unit}": pace_min.round(2),
                              "vo2max": f["vo2max"]}), key="pace")
    st.caption(f"Reference HR: {int(ref) if ref and not pd.isna(ref) else 'auto (75% of max)'} bpm · "
               f"VO2 max (Garmin): {f['vo2max'].dropna().iloc[-1] if f['vo2max'].notna().any() else '–'}")

st.subheader("Lifting")
lp = c.query("""SELECT p.date, e.name, p.best_e1rm_kg, p.top_set_kg, p.top_set_reps FROM analytics.lift_progress p
                JOIN core.exercise e ON e.id = p.exercise_id WHERE p.date >= %s ORDER BY p.date""", (start,))
left, right = st.columns(2, gap="large")
with left:
    if lp.empty:
        c.empty_state("No main-lift sets logged in this range.")
    else:
        lp["e1rm"] = lp["best_e1rm_kg"].map(lambda v: d.weight(v, 0))
        names = lp.groupby("name")["date"].count().sort_values(ascending=False).index.tolist()
        pick = st.multiselect("Lifts", names, default=names[:3], max_selections=3, key="lifts")
        fig = go.Figure()
        for name in (n for n in names if n in pick):  # color follows the lift, not its rank
            sub = lp[lp["name"] == name]
            c.line(fig, pd.to_datetime(sub["date"]), sub["e1rm"], name, slot=names.index(name) % 3,
                   mode="lines+markers", fmt=".0f")
        c.show(c.base_layout(fig, "Estimated 1RM (Epley, best set per day)", d.weight_unit),
               lp[["date", "name", "e1rm", "top_set_kg", "top_set_reps"]], key="e1rm")
with right:
    vol = c.query("""SELECT week_start, muscle_group, hard_sets FROM analytics.weekly_muscle_volume
                     WHERE week_start >= %s ORDER BY week_start""", (start,))
    if vol.empty:
        c.empty_state("No hard sets in this range.")
    else:
        piv = vol.pivot_table(index="muscle_group", columns="week_start", values="hard_sets", fill_value=0)
        fig = go.Figure(go.Heatmap(
            z=piv.values, x=[pd.Timestamp(x) for x in piv.columns], y=[m.replace("_", " ") for m in piv.index],
            colorscale=[[0, "#cde2fb"], [0.5, "#3987e5"], [1, "#0d366b"]], xgap=2, ygap=2,
            hovertemplate="%{y}, week of %{x|%b %d}: %{z:.1f} hard sets<extra></extra>",
            colorbar={"title": "sets", "thickness": 10}))
        fig = c.base_layout(fig, "Weekly hard sets per muscle group (target 10-20)", legend=False, height=360)
        fig.update_layout(hovermode="closest")
        c.show(fig, vol, key="volume")

st.subheader("Plan vs actual")
plan = c.query("""SELECT p.date, p.sport, p.session_type, p.planned_duration_min AS planned_min,
                         p.planned_rpe, p.status, p.origin,
                         round(w.duration_s / 60.0) AS actual_min, w.rpe AS actual_rpe
                  FROM core.planned_session p LEFT JOIN core.workout w ON w.id = p.linked_workout_id
                  WHERE p.date BETWEEN %s AND %s + 14 ORDER BY p.date DESC""", (end - pd.Timedelta(days=28), end))
if plan.empty:
    c.empty_state("No planned sessions yet. Add games in the app; the weekly job generates the rest.")
else:
    st.dataframe(plan, hide_index=True, width="stretch")
