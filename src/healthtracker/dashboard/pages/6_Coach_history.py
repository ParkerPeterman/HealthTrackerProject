"""Coach history: past recommendations, whether advice was followed, outcomes, rule performance."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from healthtracker.dashboard import common as c

c.setup("Coach history")
st.title("Coach history")
days = c.range_filter(default="30 days")
start, end = c.date_window(days)

perf = c.query("""SELECT r.rule_id, r.status, r.evidence_level, p.n_actions, p.followed_rate,
                         p.avg_outcome_when_followed, p.avg_outcome_when_not
                  FROM analytics.rule r LEFT JOIN analytics.v_rule_performance p USING (rule_id)
                  ORDER BY p.n_actions DESC NULLS LAST""")
st.subheader("Rule performance")
st.caption("Outcome delta is the change vs my baseline on the rule's outcome metric (positive = better). "
           "Shadow rules are graded the same way while hidden, so a rule earns its place before it's shown.")
graded = perf.dropna(subset=["avg_outcome_when_followed", "avg_outcome_when_not"])
if not graded.empty:
    fig = go.Figure()
    pal = c.palette()
    fig.add_trace(go.Bar(y=graded["rule_id"], x=graded["avg_outcome_when_followed"], name="followed",
                         orientation="h", marker={"color": pal["series"][0], "cornerradius": 4},
                         hovertemplate="followed: %{x:+.2f}<extra></extra>"))
    fig.add_trace(go.Bar(y=graded["rule_id"], x=graded["avg_outcome_when_not"], name="not followed",
                         orientation="h", marker={"color": pal["series"][1], "cornerradius": 4},
                         hovertemplate="not followed: %{x:+.2f}<extra></extra>"))
    fig.add_vline(x=0, line_width=1, line_color=pal["axis"])
    fig = c.base_layout(fig, "Average outcome delta: followed vs not", height=120 + 44 * len(graded))
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08, hovermode="y unified")
    c.show(fig, perf, key="perf")
else:
    st.dataframe(perf, hide_index=True, width="stretch")

st.subheader("Recommendations")
recs = c.query("""SELECT r.id, r.date, r.rating, r.output_check, r.llm_model, r.rendered_text,
                         count(a.id) FILTER (WHERE a.visible) AS actions,
                         count(a.id) FILTER (WHERE a.visible AND a.followed) AS followed,
                         count(a.id) FILTER (WHERE a.visible AND a.followed IS NOT NULL) AS graded
                  FROM analytics.recommendation r
                  LEFT JOIN analytics.recommendation_action a ON a.recommendation_id = r.id
                  WHERE r.kind = 'daily' AND r.date BETWEEN %s AND %s
                  GROUP BY r.id ORDER BY r.date DESC""", (start, end))
if recs.empty:
    c.empty_state()
else:
    rated = recs.dropna(subset=["rating"])
    cols = st.columns(4)
    cols[0].metric("Recommendations", len(recs))
    cols[1].metric("Visible actions followed",
                   f"{recs['followed'].sum() / recs['graded'].sum():.0%}" if recs["graded"].sum() else "–")
    cols[2].metric("Average rating", f"{rated['rating'].mean():.1f} / 5" if not rated.empty else "–")
    llm = recs["output_check"].value_counts()
    cols[3].metric("LLM output passed check",
                   f"{llm.get('passed', 0)} of {llm.sum()}" if llm.sum() else "template only")
    for r in recs.head(14).itertuples():
        title = f"{pd.Timestamp(r.date):%a %b %d} · {r.actions} actions"
        if r.graded:
            title += f" · {r.followed}/{r.graded} followed"
        if r.rating and not pd.isna(r.rating):
            title += f" · rated {int(r.rating)}/5"
        with st.expander(title):
            st.markdown(r.rendered_text or "_no message_")
            acts = c.query("""SELECT a.rule_id, a.visible, a.action_text, a.metric, a.comparator, a.target_value,
                                     a.evaluate_on, a.actual_value, a.followed, a.outcome_metric, a.outcome_delta
                              FROM analytics.recommendation_action a WHERE a.recommendation_id = %s
                              ORDER BY a.visible DESC, a.id""", (r.id,))
            if not acts.empty:
                st.dataframe(acts, hide_index=True, width="stretch")

st.subheader("Rule changes")
hist = c.query("""SELECT changed_at, 'parameter' AS kind, rule_id, name, old_value::text AS old,
                         new_value::text AS new, proposed_by, approved, reason
                  FROM analytics.rule_parameter_history
                  UNION ALL
                  SELECT changed_at, 'status', rule_id, NULL, old_status, new_status, proposed_by, approved, reason
                  FROM analytics.rule_status_change ORDER BY changed_at DESC LIMIT 50""")
if hist.empty:
    st.caption("No parameter or status changes yet. The weekly job proposes them; you approve in the app.")
else:
    hist["approved"] = hist["approved"].map({True: "approved", False: "rejected"}).fillna("pending")
    st.dataframe(hist, hide_index=True, width="stretch")
