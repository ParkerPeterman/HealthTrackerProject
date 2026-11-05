"""Insights: supported personal findings with effect sizes, all hypotheses, experiments."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from healthtracker.dashboard import common as c
from healthtracker.stats.hypothesis import LABELS, Hypothesis, describe

c.setup("Insights")
st.title("Insights")
st.caption("A finding is **supported** only with ≥20 days per group, BH-adjusted p < 0.05, a 95% CI that excludes "
           "zero under HAC errors and a block bootstrap, an effect ≥ 0.2 SD, and replication in later data. "
           "Controls: day of week, prior night's sleep, training load. Confidence words are fixed: under 0.6 "
           "*too early to tell*, 0.6-0.8 *possibly*, 0.8-0.95 *likely*, 0.95 or more *very likely* (the probability "
           "that the effect's direction is right).")

rows = c.query("""SELECT h.id, h.exposure, h.outcome, h.lag_days, h.status AS hyp_status, i.status, i.summary,
                         i.effect_size, i.ci_low, i.ci_high, i.p_value, i.p_adjusted, i.n_exposed, i.n_unexposed,
                         i.replicated, i.computed_at, i.window_start, i.window_end,
                         i.confidence, i.probability, i.record->>'text' AS claim
                  FROM analytics.hypothesis h
                  LEFT JOIN LATERAL (SELECT * FROM analytics.insight x WHERE x.hypothesis_id = h.id
                                     ORDER BY computed_at DESC LIMIT 1) i ON true
                  WHERE h.status <> 'retired'""")
if rows.empty or rows["status"].isna().all():
    c.empty_state("The weekly stats job hasn't run yet (needs about 20 days per group for the first tests).")
    st.stop()

labels = []
for r in rows.itertuples():
    exp, out = describe(Hypothesis(r.id, r.exposure, r.outcome, r.lag_days))
    labels.append(f"{exp} → {out}")
rows["finding"] = labels
last = rows["computed_at"].max()
st.caption(f"Last computed {pd.Timestamp(last):%b %d %H:%M}")

counts = rows["status"].value_counts()
cols = st.columns(3)
cols[0].metric("Supported", int(counts.get("supported", 0)))
cols[1].metric("Weak / unconfirmed", int(counts.get("weak", 0)))
cols[2].metric("Waiting for data", int(counts.get("insufficient_data", 0)))

sup = rows[rows["status"] == "supported"].copy()
st.subheader("Supported findings")
if sup.empty:
    c.empty_state("Nothing has cleared every check yet. That's the system being careful, not broken.")
else:
    for outcome, grp in sup.groupby("outcome"):
        fig = go.Figure()
        color = c.palette()["series"][0]
        grp = grp.sort_values("effect_size")
        fig.add_trace(go.Scatter(
            x=grp["effect_size"], y=grp["finding"], mode="markers", name="effect",
            marker={"size": 10, "color": color},
            error_x={"type": "data", "symmetric": False, "array": grp["ci_high"] - grp["effect_size"],
                     "arrayminus": grp["effect_size"] - grp["ci_low"], "thickness": 2, "width": 0, "color": color},
            customdata=grp[["n_exposed", "n_unexposed", "p_adjusted"]],
            hovertemplate="%{x:+.2f} (n=%{customdata[0]} vs %{customdata[1]}, adj. p=%{customdata[2]:.3f})"
                          "<extra></extra>"))
        fig.add_vline(x=0, line_width=1, line_color=c.palette()["axis"])
        fig = c.base_layout(fig, f"Effect on {LABELS.get(outcome, outcome.replace('_', ' '))} (95% CI)", legend=False,
                            height=90 + 48 * len(grp))
        fig.update_layout(hovermode="closest")
        c.show(fig, grp[["finding", "effect_size", "ci_low", "ci_high", "n_exposed", "n_unexposed", "p_adjusted"]],
               key=f"forest-{outcome}")

st.subheader("All hypotheses")
table = rows[["finding", "status", "n_exposed", "n_unexposed", "effect_size", "ci_low", "ci_high", "p_adjusted",
              "replicated", "summary"]].sort_values(["status", "p_adjusted"], na_position="last")
st.dataframe(table, hide_index=True, width="stretch",
             column_config={"summary": st.column_config.TextColumn(width="large")})

st.subheader("Bayesian estimates (B1, shadow)")
est = c.query("""SELECT h.exposure, h.outcome, h.lag_days, e.kind, e.n_days, e.estimate, e.lo80, e.hi80, e.lo95, e.hi95,
                        e.p_direction, e.swc, e.p_meaningful, e.days_to_80, e.rho, e.prior_mean, e.prior_sd,
                        e.prior_source
                 FROM analytics.effect_estimate e JOIN analytics.hypothesis h ON h.id = e.hypothesis_id
                 WHERE e.computed_on = (SELECT max(computed_on) FROM analytics.effect_estimate)
                 ORDER BY e.p_meaningful DESC NULLS LAST""")
if est.empty:
    c.empty_state("No Bayesian estimates yet (the weekly job computes them).")
else:
    st.caption("Shadow mode: computed weekly next to the tests above, not used for advice until COMP_BAYES_EFFECTS "
               "is turned on. p_meaningful is P(|effect| > the smallest worthwhile change); days_to_80 is how many "
               "more days until it would reach 0.8 if the estimate holds. Doses are per unit.")
    st.dataframe(est, hide_index=True, width="stretch")

st.subheader("Sleep drivers (E3, shadow)")
drv = c.query("""SELECT outcome, exposure, n_days, estimate, lo80, hi80, p_direction, p_meaningful, prior_source
                 FROM analytics.sleep_driver WHERE computed_on = (SELECT max(computed_on) FROM analytics.sleep_driver)
                 ORDER BY outcome, p_meaningful DESC""")
if drv.empty:
    c.empty_state("No sleep drivers model yet (needs about 30 complete food-log days).")
else:
    st.caption("One model per sleep outcome with every exposure in it, so effects aren't double-counted. Estimates "
               "are per unit of exposure (drinks, mg, hours, load units); travel is per travel day.")
    st.dataframe(drv, hide_index=True, width="stretch")

st.subheader("Self-experiments")
exp = c.query("""SELECT e.id, h.exposure, h.outcome, e.design, e.start_date, e.end_date, e.status, e.result_summary
                 FROM analytics.experiment e LEFT JOIN analytics.hypothesis h ON h.id = e.hypothesis_id
                 ORDER BY e.start_date DESC""")
if exp.empty:
    c.empty_state("No experiments yet. Start one from a promising hypothesis in the app (Coach tab).")
else:
    st.dataframe(exp, hide_index=True, width="stretch")
