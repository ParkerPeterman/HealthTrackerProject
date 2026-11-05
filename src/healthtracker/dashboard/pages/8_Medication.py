"""Medication (sensitive, H5): hidden until turned on for this session."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from healthtracker.dashboard import common as c

c.setup("Medication")
st.title("Medication")
st.caption("Sensitive. Nothing here is shown unless you turn it on, and it is never sent to the coaching model.")
if not st.toggle("Show medication data", value=False, key="show_medication"):
    st.stop()

days = c.range_filter()
start, end = c.date_window(days)
meds = c.query("SELECT id, name, dose_mg, start_date, active FROM core.medication ORDER BY id")
if meds.empty:
    c.empty_state("No medications. Add one in the app (Settings).")
    st.stop()
for m in meds.itertuples():
    st.subheader(f"{m.name}{f' · {m.dose_mg:g} mg' if m.dose_mg else ''}")
    f = c.query("""SELECT date, med_status, adherence_7d, known_7d, adherence_28d, consecutive_missed, dose_mg,
                          dose_time_local FROM analytics.medication_daily
                   WHERE medication_id = %s AND date BETWEEN %s AND %s ORDER BY date""", (m.id, start, end))
    if f.empty:
        c.empty_state()
        continue
    fig = go.Figure()
    c.line(fig, f["date"], (f["adherence_28d"] * 100).round(), "28-day share taken (unknown days left out)", fmt=".0f")
    c.dots(fig, f.loc[f["med_status"] == "missed", "date"], [0] * int((f["med_status"] == "missed").sum()),
           "recorded miss", slot=2, fmt=".0f")
    c.show(c.base_layout(fig, "Adherence", "% of recorded days"), f, key=f"adh{m.id}")
    msgs = c.query("""SELECT date, template, text, visible FROM analytics.medication_message
                      WHERE medication_id = %s AND date BETWEEN %s AND %s ORDER BY date DESC""", (m.id, start, end))
    if not msgs.empty:
        st.dataframe(msgs, hide_index=True, width="stretch")
