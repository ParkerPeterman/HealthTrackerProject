"""Trends: HRV, resting HR, sleep, and weight with 7- and 30-day lines."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from healthtracker.dashboard import common as c

c.setup("Trends")
st.title("Trends")
days = c.range_filter()
f = c.features(days)
if f.empty:
    c.empty_state()
    st.stop()
d = c.display()

left, right = st.columns(2, gap="large")
with left:
    fig = go.Figure()
    c.dots(fig, f["date"], f["hrv"], "nightly", slot=0, fmt=".0f")
    c.line(fig, f["date"], f["hrv_7d_avg"], "7-day", slot=0)
    c.line(fig, f["date"], f["hrv_30d_avg"], "30-day", slot=1)
    c.show(c.base_layout(fig, "Overnight HRV", "ms"),
           f[["date", "hrv", "hrv_7d_avg", "hrv_30d_avg", "hrv_z_30d"]], key="hrv")
with right:
    rhr = f["resting_hr"]
    fig = go.Figure()
    c.dots(fig, f["date"], rhr, "daily", slot=0, fmt=".0f")
    c.line(fig, f["date"], rhr.rolling(7, min_periods=3).mean(), "7-day", slot=0)
    c.line(fig, f["date"], rhr.rolling(30, min_periods=10).mean(), "30-day", slot=1)
    c.show(c.base_layout(fig, "Resting heart rate", "bpm"), f[["date", "resting_hr", "rhr_z_30d"]], key="rhr")

left, right = st.columns(2, gap="large")
with left:
    hours = f["sleep_total_min"] / 60
    fig = go.Figure()
    c.dots(fig, f["date"], hours, "nightly", slot=0, fmt=".1f")
    c.line(fig, f["date"], hours.rolling(7, min_periods=3).mean(), "7-day", slot=0)
    c.band(fig, 7, 9, "7-9 h")
    c.show(c.base_layout(fig, "Sleep duration", "hours"), f[["date", "sleep_total_min", "sleep_midpoint_min"]],
           key="sleep")
with right:
    fig = go.Figure()
    c.dots(fig, f["date"], f["sleep_score"], "nightly", slot=0, fmt=".0f")
    c.line(fig, f["date"], f["sleep_score"].rolling(7, min_periods=3).mean(), "7-day", slot=0)
    c.show(c.base_layout(fig, "Sleep score", "score"), f[["date", "sleep_score", "sleep_score_z_30d"]], key="score")

w = f[["date", "weight_kg", "weight_kg_smoothed"]].copy()
w["weight"] = w["weight_kg"].map(lambda v: d.weight(v))
w["trend"] = w["weight_kg_smoothed"].map(lambda v: d.weight(v))
fig = go.Figure()
c.dots(fig, w["date"], w["weight"], "scale", slot=0)
c.line(fig, w["date"], w["trend"], "trend (EWMA)", slot=0)
goal = c.query("""SELECT target_value, unit FROM core.goal WHERE goal_type = 'body_weight' AND active
                  ORDER BY id DESC LIMIT 1""")
if not goal.empty and goal.iloc[0]["unit"] == "kg":
    target = d.weight(goal.iloc[0]["target_value"])
    c.band(fig, target, target, f"goal {target:g} {d.weight_unit}")
c.show(c.base_layout(fig, "Body weight", d.weight_unit, height=320), w[["date", "weight", "trend"]], key="weight")
