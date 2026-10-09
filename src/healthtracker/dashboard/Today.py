"""Today: recovery vs my baseline, today's recommendation, flags that fired."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from healthtracker.dashboard import common as c
from healthtracker.units import fmt_hour

c.setup("Today")
st.title("Today")

rec = c.query("""SELECT * FROM analytics.recommendation WHERE kind = 'daily' ORDER BY date DESC LIMIT 1""")
f = c.features(40)
if f.empty:
    c.empty_state("No daily_features yet. Run `healthtracker nightly` (or `healthtracker demo`) first.")
    st.stop()

today = f.iloc[-1]
d = c.display()
st.caption(f"{today['date']:%A, %B %d}  ·  data through the morning of this day")


def _v(x, fmt="{:.0f}", none="–"):
    return none if x is None or pd.isna(x) else fmt.format(x)


cols = st.columns(4)
level, text = c.z_status(today.get("hrv_z_30d"))
cols[0].metric("Overnight HRV", f"{_v(today['hrv'])} ms",
               help="30-day baseline: " + _v(today.get("hrv_30d_avg"), "{:.0f} ms"))
cols[0].markdown(c.status_chip(level, text), unsafe_allow_html=True)
level, text = c.z_status(today.get("rhr_z_30d"), higher_is_better=False)
cols[1].metric("Resting HR", f"{_v(today['resting_hr'])} bpm")
cols[1].markdown(c.status_chip(level, text), unsafe_allow_html=True)
sleep_min = today.get("sleep_total_min")
level, text = c.z_status(today.get("sleep_score_z_30d"))
cols[2].metric("Sleep", "–" if pd.isna(sleep_min) else f"{int(sleep_min) // 60}h{int(sleep_min) % 60:02d}",
               help=f"Score {_v(today.get('sleep_score'))}")
cols[2].markdown(c.status_chip(level, f"score {_v(today.get('sleep_score'))}, {text}"), unsafe_allow_html=True)
y = f.iloc[-2] if len(f) > 1 else today
acwr = y.get("acwr")
if acwr is None or pd.isna(acwr):
    lvl, txt = "warning", "building history"
elif acwr > 1.5:
    lvl, txt = "critical", f"ACWR {acwr:.2f} (spike)"
elif acwr > 1.3:
    lvl, txt = "serious", f"ACWR {acwr:.2f} (high)"
elif acwr < 0.8:
    lvl, txt = "warning", f"ACWR {acwr:.2f} (detraining)"
else:
    lvl, txt = "good", f"ACWR {acwr:.2f} (sweet spot)"
cols[3].metric("7-day load", _v(y.get("acute_load_7d")), help="session RPE x minutes, last 7 days")
cols[3].markdown(c.status_chip(lvl, txt), unsafe_allow_html=True)

st.divider()
left, right = st.columns([3, 2], gap="large")
with left:
    st.subheader("Today's coaching")
    if rec.empty:
        c.empty_state("No recommendation yet today.")
    else:
        r = rec.iloc[0]
        meta = []
        if r["llm_model"]:
            meta.append(f"phrased by {r['llm_model']}")
        if r["output_check"] == "failed_fallback_used":
            meta.append("LLM output failed its check; template used")
        elif r["output_check"] is None:
            meta.append("template")
        if r["rating"] and not pd.isna(r["rating"]):
            meta.append(f"rated {int(r['rating'])}/5")
        st.caption(" · ".join(meta))
        st.markdown(r["rendered_text"] or "_No message._")

    week = c.query("""SELECT rendered_text FROM analytics.recommendation WHERE kind = 'weekly'
                      ORDER BY date DESC LIMIT 1""")
    if not week.empty and week.iloc[0]["rendered_text"]:
        with st.expander("This week's plan", expanded=False):
            st.markdown(week.iloc[0]["rendered_text"])

with right:
    st.subheader("Rules that fired")
    if rec.empty:
        st.write("–")
    else:
        dec = rec.iloc[0]["decisions"]
        dec = json.loads(dec) if isinstance(dec, str) else dec
        fired = dec.get("fired", [])
        sev_level = {1: "good", 2: "warning", 3: "critical"}
        sev_name = {1: "info", 2: "caution", 3: "important"}
        shown = [x for x in fired if x.get("visible")]
        if not shown:
            st.markdown(c.status_chip("good", "nothing flagged"), unsafe_allow_html=True)
        for x in shown:
            st.markdown(f"{c.status_chip(sev_level.get(x['severity'], 'warning'), sev_name.get(x['severity'], ''))} "
                        f"**{x['rule_id']}**  \n<span style='opacity:.8'>{x['description']}</span>",
                        unsafe_allow_html=True)
        hidden = [x for x in fired if not x.get("visible")]
        if hidden:
            with st.expander(f"Shadow rules ({len(hidden)}): logged and graded, not shown in coaching"):
                for x in hidden:
                    st.markdown(f"- **{x['rule_id']}**: {x['description']}")
        if dec.get("dormant"):
            st.caption(f"Dormant until enough history: {', '.join(dec['dormant'])} "
                       f"({dec.get('history_days', 0)} days so far)")

    st.subheader("Yesterday")
    yrow = f.iloc[-2] if len(f) > 1 else None
    if yrow is not None:
        bits = []
        if yrow.get("food_log_complete"):
            bits.append(f"{_v(yrow['kcal'])} kcal · {_v(yrow['protein_g'])} g protein "
                        f"({_v(yrow['protein_g_per_kg'], '{:.1f}')} g/kg)")
            if not pd.isna(yrow.get("last_caffeine_hour")):
                bits.append(f"last caffeine {fmt_hour(yrow['last_caffeine_hour'])}")
        else:
            bits.append("food log not marked complete")
        bits.append(f"load {_v(yrow['session_load'])}" + (" · game" if yrow.get("had_game") else ""))
        if not pd.isna(yrow.get("weight_kg_smoothed")):
            bits.append(f"weight trend {d.weight(yrow['weight_kg_smoothed'])} {d.weight_unit}")
        st.markdown("  \n".join(bits))
