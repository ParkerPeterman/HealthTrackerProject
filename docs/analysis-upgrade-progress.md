# Analysis upgrade: progress and where to pick up

Working notes for building the analysis & coaching upgrade brief (`docs/analysisupgrade.md`,
kept out of git because it names personal health details). Branch: **`analysis-upgrade`**,
one commit per work item, nothing pushed. Personal details (medication, injuries, max HR)
never go into code, migrations, tests, or commits; they live only in the database.

## Done (committed, tested)

| Item | What it added | Migration |
|---|---|---|
| A1 | `healthtracker coverage`: days missing sleep/HRV/resting HR, which Garmin fields are stored | – |
| A8 | Performance tests under Train (max HR, 5 km, 10/30 m sprints, CMJ, broad jump); sprint and jump goals | 007 |
| D3 (zones half) | Versioned `core.athlete_zones`; chest strap or checked wrist test confirms max HR (held 30 s, smooth rise, not cadence); no age formula; FIT cadence + altitude | 008 |
| A4 | Quality/context flags (watch overnight, plausible sleep, sick, travel, tags, tz change, DST week); one-tap tags on Today; per-hypothesis `excludes` | 009 |
| A3 + A2 | `analytics.metric_status`: robust baselines that exclude the acute week, maturity; 7-day trimmed ln-HRV / RHR vs baseline ± 0.5 SD band; `COMP_HRV_BANDS` switch (shadow) | 010 |
| A5 | `analytics.session_features`: session type, time in zones, pace at ref HR, GAP, decoupling, HR drift | 011 |
| A6 | Banister TRIMP; session-RPE calibrated to it; `analytics.load_daily` channels (internal, run/game/lift/other, plyo, sprint, shoulder) | 012 |
| D1 | Uncoupled acute/chronic EWMAs, weekly change; `TRAIN_RUN_RAMP`, `TRAIN_CHANNEL_RAMP` via shared `planning/ramp.py`; ACWR descriptive only | 013 |
| D4 | `analytics.intensity_weekly`, 14-day easy/moderate shares, `TRAIN_INTENSITY_MIX` | 014 |
| F1 | Season phases (`core.season`, `core.season_phase`) from the first game (default Apr 1); Settings card; phase on Train and in `RuleContext.phase` | 015 |
| F2 | `knowledge.session_library` (29 sessions + sprint technique), fit checks and exercise swaps (`planning/library.py`), Copenhagen levels, sore-shoulder tag + daily 0-10 check-in, `INJ_SHOULDER_SWAP`, `INJ_PHYSIO_PROMPT` | 016 |
| F3 | Template planner (`planning/templates.py`, `planning/weekly.py`): greedy placement, spacing, ramp caps, structured reasons, versions (`analytics.week_plan`), weekly grading, VO2 3x3 -> 4x4 progression; Settings: main days, minutes, travel-day slots | 017 |
| H1, H2, H5, H4 display | `core.medication*`, `analytics.medication_daily`, `analytics.medication_message`; Today check + yesterday question + badge; approved templates only, after the LLM guard; dashboard opt-in page; 988 in Settings; guard bans medication/painkiller/extra-water wording | 018 |
| A7 | Open-Meteo (`ingest/weather.py`): rounded point + dates only; forecast + archive, cached in `raw.ingest`; session weather; planner icy/very-cold swaps; attribution; README Privacy | 019 |
| D3 trend | `features/fitness.py`: pace at ref HR adjusted (Normal(0, 0.5 SD) priors), statsmodels local linear trend with 80% intervals, smoothed VO2 max, decoupling vs 5% | 020 |
| E1 | `features/sleep.py`: Phillips SRI (sleep windows + naps, local clock), midpoint SD, weekend gap; 7/28 days | 021 |
| E2 | `knowledge.feature_quality` tiers, `analytics.rule.inputs`; poor-only rules can't go active, poor-tier insights never supported; journal `sleep_quality` replaces motivation in the app | 022 |
| C5 | Insight records (`stats/records.py`), fixed confidence words, guard rejects words not in the records | 023 |
| B1-B3 | `stats/bayes.py` (NIG after AR(1); design/fit/summarize; log evidence; `ar=False` for run-level data), `knowledge.effect_priors`, `ANALYSIS_SWC`, dose hypotheses | 024 |
| G1 v1 | `features/readiness.py`, `analytics.readiness_daily` (shadow) | 025 |
| F4 | `TRAIN_DAILY_ADJUST` shadow rule (no medication inputs) | 026 |
| H3 | `medication/analysis.py`, `analytics.medication_effect` (sensitive, shadow) | 027 |
| E3 | `stats/sleep_drivers.py`, `analytics.sleep_driver` (shadow) | 028 |
| B7 | `stats/designs.py`: Mon-Thu / Fri-Sun blocks, stratified randomization, suggested experiment | 029 |
| E5 | `RECOV_POSSIBLE_ILLNESS` shadow rule | 030 |
| B6 | `stats/screen.py`, `analytics.screen_result` (prior 0.2 SD, not 0.1) | 031 |
| C1-C4 | `stats/insight_types.py`, `stats/ranking.py`, `analytics.weekly_insight` with "new to me / knew it" rating (shadow) | 032 |
| D2 | `features/fitness_fatigue.py`, backtest vs naive, 8-week forecast (shadow) | 033 |
| B5 | `stats/pooling.py`, pooled estimates on `effect_estimate` | 034 |
| D5 | `features/session_response.py`; planner keeps `recovery_hours` before games | 035 |
| E4 | `features/long_term.py`, `analytics.long_trend`, `analytics.health_weekly` | 036 |

## Next, in order

1. **F5-F7** in `planning/weekly.py` (designed, not started):
   - F5: progression factor only when last week's key sessions were done (week_plan grade) and no caution, else hold at 1.0; a deload every Nth week of a phase (`deload_every`, default 4): deload factor, low-end durations, VO2 level held; strength load suggestions from the last session's RIR (+5% at RIR 3+, +2.5% at 2, hold at 1, -5% at 0) shown in the library detail.
   - F6: a plan `health` block from planned sessions (moderate/vigorous minutes, strength days vs WHO, from E4's rules); extend easy runs toward 150 equivalent minutes within the caps; a sleep-regularity note when SRI is low; the protein target in the summary.
   - F7: pre-season return stage by week (1-2: accelerations only, no max velocity, cutting, or game-like; 3-4: max velocity and cutting; 5+: everything), sprint metres scaled 0.5 / 0.75 / 1.0 before the ramp caps.
2. **G2** forecasts (fitness via D2/D3, Kalman weight trend: check `goals` first, goal ETAs as date ranges; each must beat naive in rolling backtests), **G3** Bayesian online change points (medication dose changes stay in the sensitive tables), **G4** anomaly explanations from drivers and A4 flags, **G5** fair grading of advice (state-matched followed vs not, with intervals), **B4** lagged effects (exponential-decay lags, tau chosen by `log_ev`).
3. **Section 13**: plant the remaining demo effects (interaction, change point, temperature effect on HR, groin tweaks) on separate RNG streams; run methods at 14/30/60/120 days; a weekly shadow-comparison log; a runtime check (nightly < 2 min, weekly < 10 min).
4. README (Privacy is already updated) and design-notes: spacing rule, the medication/LLM separation, B1 priors, shadow switches.

## Judgment calls to raise with the owner

- **VO2 build spacing.** The brief puts the two VO2 sessions on Wed and Thu, which breaks "no back-to-back hard days". The template uses Tue and Thu. Default spacing also lets a hard endurance day follow a sprint/strength day (otherwise only two hard days fit in Mon-Thu); `ANALYSIS_PLAN.strict_spacing = 1` forbids any adjacent hard days.
- **Safety templates live.** The dizziness-in-training and lowest-mood (988) medication messages are live (`COMP_MED_SAFETY`), alongside the adherence display; the readiness template is in shadow.
- **Journal**: mood and energy are among the journal taps, which answers the brief's open question for H4.
- **Medication never feeds coaching rules.** F4 ignores the medication readiness flag (it acts only through its own template); dose time is analyzed only in H3, not in E3.
- **Screen prior 0.2 SD** instead of the brief's 0.1 example: 0.1 hid a planted per-drink effect in 90 demo days.

## Decisions already made by the owner

- Build the whole brief; one feature branch, commit per item, owner pushes.
- Journal: **sleep quality replaces motivation** (motivation history kept).
- Travel counts **only when tapped** (no automatic Fri–Sun flag).
- Medication message templates approved as written:
  1. "{name}: taken {k} of the last 7 days ({u} not recorded)" (unknown days never count as missed)
  2. "You've missed {n} days in a row. Follow your prescriber's instructions for missed doses."
  3. (shadow 2 weeks) "Missed doses and {low energy | low mood | dizziness} today, so today counts as a lower-readiness day: no max sprints, heavy lifts, or intervals."
  4. "You logged dizziness during training. If it happens again, stop the session, and tell your prescriber."
  5. "Your mood has been at the lowest rating for {n} days. Consider checking in with your prescriber or someone you trust. If you're in crisis, call or text 988 (Suicide & Crisis Lifeline)."
  6. "On days you take {name} later, {outcome} is {effect} ({confidence}). It may be worth asking your prescriber whether timing matters."
  7. "After a missed dose, {outcome} was {effect} ({confidence})." / "Too early to tell whether missed doses affect {outcome}."
  8. "Missed doses mostly fell on travel days, so their effect can't be separated from travel."
- Push notifications not built (they need an outbound push service; ask before adding).
- No PyMC / OR-Tools so far: conjugate Normal-Inverse-Gamma updates, statsmodels Kalman filters, greedy planner.

## Conventions in the code

- Analysis settings are `ANALYSIS_*` pseudo-rules in `analytics.rule` (domain 'analysis') with values in `rule_parameter`; the rules engine skips them.
- Shadow/live switches for non-rule features are `COMP_*` rule rows; check with `components.is_live()`. Initial statuses follow the brief's phasing table.
- Demo plants use deterministic tweaks or separate random streams (`seed + 101`, …) so earlier planted effects don't move.
- Views defined with `SELECT *` must be recreated when their table gains columns (see migration 011).
- pandas 3 may store datetimes in microseconds: use `.dt.total_seconds()`, not `astype("int64")`.
- Cast float parameters to `::numeric` before `round(x, n)` in SQL.

## Testing on this machine

No Docker or system Postgres. A self-contained Postgres 16 (from the `pgserver` pip package, in a scratch venv) runs on `127.0.0.1:55432`, TCP only:

```bash
BIN=<scratch>/pgvenv/lib/python3.12/site-packages/pgserver/pginstall/bin
$BIN/initdb -D <scratch>/pgdata -U health --auth=trust -E UTF8 --locale=C.UTF-8   # first time only
$BIN/pg_ctl -D <scratch>/pgdata -o "-p 55432 -c unix_socket_directories='' -c listen_addresses=127.0.0.1 -c fsync=off" -l <scratch>/pg.log start
export TEST_DATABASE_URL=postgresql://health@127.0.0.1:55432/healthtracker_test
.venv/bin/pytest -q                  # ~263 tests, under two minutes
cd web && npx tsc -b                 # typecheck the app
```

The scratch folder is per session, so a new session recreates the venv (`uv venv pgvenv && uv pip install --python pgvenv/bin/python pgserver`) and runs `initdb` again.
