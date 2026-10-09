"""Pipeline: recent job runs, failures, ingest volume, and data coverage."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from healthtracker.dashboard import common as c

c.setup("Pipeline")
st.title("Pipeline")

runs = c.query("""SELECT id, job, step, started_at, finished_at, status, rows_written,
                         extract(epoch FROM finished_at - started_at) AS seconds, error
                  FROM analytics.pipeline_run ORDER BY id DESC LIMIT 200""")
if runs.empty:
    c.empty_state("No pipeline runs yet.")
    st.stop()

failed = runs[runs["status"] == "failed"]
latest = runs.iloc[0]
cols = st.columns(3)
cols[0].metric("Last run", f"{latest['job']} · {pd.Timestamp(latest['started_at']):%b %d %H:%M}")
recent_fail = failed[pd.to_datetime(failed["started_at"], utc=True) > pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=7)]
level = "good" if recent_fail.empty else "critical"
cols[1].markdown(c.status_chip(level, f"{len(recent_fail)} failed steps in 7 days"), unsafe_allow_html=True)
cols[2].metric("Steps logged", len(runs))

if not failed.empty:
    st.subheader("Failures")
    for r in failed.head(10).itertuples():
        with st.expander(f"{pd.Timestamp(r.started_at):%b %d %H:%M} · {r.job}/{r.step}"):
            st.code(r.error or "", language="text")

st.subheader("Recent runs")
st.dataframe(runs.drop(columns=["error"]).head(60), hide_index=True, width="stretch")

st.subheader("Data coverage (last 60 days)")
f = c.features(60)
if not f.empty:
    cov = pd.DataFrame({
        "date": f["date"],
        "overnight data": f["hrv"].notna(),
        "full-day wear": ~f["is_partial_day"].fillna(True).astype(bool),
        "food log complete": f["food_log_complete"].fillna(False).astype(bool),
        "journal": f["energy"].notna(),
        "weight": f["weight_kg"].notna(),
    })
    pct = {k: f"{cov[k].mean():.0%}" for k in cov.columns if k != "date"}
    st.caption(" · ".join(f"{k}: {v}" for k, v in pct.items()))
    z = cov.drop(columns="date").T.astype(int).values
    fig = go.Figure(go.Heatmap(z=z, x=cov["date"], y=list(cov.columns[1:]),
                               colorscale=[[0, c.palette()["neutral"]], [1, c.palette()["series"][0]]],
                               showscale=False, xgap=2, ygap=2,
                               hovertemplate="%{y}, %{x|%b %d}: %{z}<extra>1 = present</extra>"))
    fig = c.base_layout(fig, "Present (blue) or missing (gray) per day", legend=False, height=260)
    fig.update_layout(hovermode="closest")
    c.show(fig, cov, key="coverage")

raw = c.query("""SELECT source, endpoint, for_date, count(*) AS payloads, max(fetched_at) AS last_fetched
                 FROM raw.ingest WHERE for_date > now()::date - 14 GROUP BY 1, 2, 3 ORDER BY for_date DESC, 1, 2""")
st.subheader("Raw payloads (last 14 days)")
st.dataframe(raw, hide_index=True, width="stretch")
