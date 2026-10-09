"""Injuries: active injuries, pain trend, current restrictions."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from healthtracker.dashboard import common as c

c.setup("Injuries")
st.title("Injuries")
show_resolved = st.toggle("Include resolved", value=True)
inj = c.query("""SELECT * FROM core.injury WHERE %s OR status <> 'resolved'
                 ORDER BY status = 'resolved', onset_date DESC""", (show_resolved,))
if inj.empty:
    c.empty_state("No injuries logged. Good.")
    st.stop()

today = c.last_date()
for r in inj.itertuples():
    side = f"{r.side} " if r.side and r.side != "n/a" else ""
    level = {"active": "critical", "recovering": "warning", "resolved": "good"}[r.status]
    with st.container(border=True):
        st.markdown(f"### {side}{r.body_part} · {r.injury_type}  {c.status_chip(level, r.status)}",
                    unsafe_allow_html=True)
        meta = [f"onset {r.onset_date:%b %d}"]
        if r.resolved_date and not pd.isna(r.resolved_date):
            meta.append(f"resolved {r.resolved_date:%b %d} ({(r.resolved_date - r.onset_date).days} days)")
        meta.append("seen a professional" if r.seen_professional else "not seen by a professional")
        if r.mechanism:
            meta.append(r.mechanism)
        st.caption(" · ".join(meta))
        ci = c.query("SELECT * FROM core.injury_checkin WHERE injury_id = %s ORDER BY date", (r.id,))
        left, right = st.columns([3, 2], gap="large")
        with left:
            if ci.empty:
                c.empty_state("No check-ins yet.")
            else:
                ci["date"] = pd.to_datetime(ci["date"])
                fig = go.Figure()
                c.line(fig, ci["date"], ci["pain_activity"], "pain during activity", slot=0,
                       mode="lines+markers", fmt=".0f")
                c.line(fig, ci["date"], ci["pain_rest"], "pain at rest", slot=1, mode="lines+markers", fmt=".0f")
                c.band(fig, 0, 3, "return-to-play zone (0-3)")
                fig = c.base_layout(fig, "Pain (0-10)", "pain", height=260)
                fig.update_yaxes(range=[0, 10])
                c.show(fig, ci[["date", "pain_rest", "pain_activity", "function_score", "swelling", "note"]],
                       key=f"pain{r.id}")
        with right:
            rs = c.query("""SELECT r.target_type, coalesce(e.name, r.muscle_group, r.movement_pattern, r.activity)
                                   AS target, r.max_rpe, r.start_date, r.end_date, r.origin
                            FROM core.injury_restriction r LEFT JOIN core.exercise e ON e.id = r.exercise_id
                            WHERE r.injury_id = %s ORDER BY r.start_date""", (r.id,))
            st.markdown("**Restrictions**")
            if rs.empty:
                st.caption("none")
            else:
                rs["limit"] = rs["max_rpe"].map(lambda v: "avoid" if v == 0 else ("–" if pd.isna(v) else f"RPE ≤ {int(v)}"))
                rs["active"] = [(s <= today) and (e is None or pd.isna(e) or e >= today)
                                for s, e in zip(rs["start_date"], rs["end_date"], strict=False)]
                st.dataframe(rs[["target", "limit", "start_date", "end_date", "origin", "active"]],
                             hide_index=True, width="stretch")
