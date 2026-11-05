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
| F5 | `planning/progression.py`: load grows only after a graded week with key sessions done and recovery held (else holds at 1.0); every 4th week of base / VO2 build / pre-season is a deload (key sessions at the low end, the rest x deload factor, fewer sprint and jump reps, optional sessions dropped, VO2 held); lift suggestions from last session's RIR in the library detail | 037 |
| F6 | `planning/health.py`: plan `health` block (WHO minutes and strength days from planned sessions, main days vs with optional), easy runs lengthen toward 150 inside the caps (not in deloads or after flags), SRI note, protein in g a day | 038 |
| F7 | `session_library.return_stage`; pre-season weeks 1-2 accelerations only, 3-4 + max velocity and cutting, 5+ everything; sprint m x 0.5 / 0.75 / 1.0 before the caps; Thursday falls back to hill repeats | 039 |
| G2 | `stats/forecast.py`, `analytics.forecast`: local linear trend per series + D2 under the plan, rolling-origin backtests vs naive, goal arrival date ranges (`eta` on goal progress), `/api/forecasts`; `COMP_FORECASTS` shadow | 040 |
| G3 | `stats/changepoint.py`: BOCPD (Normal-Gamma, AR(1) prewhitened), candidate causes, `analytics.change_point`, sensitive `analytics.change_point_medication`, `/api/change-points`; `COMP_CHANGE_POINTS` shadow | 041 |
| G4 | `stats/anomaly.py`: unusual mornings (2+ robust SD) explained from quality/context flags, my own credible B1 estimates at their lags (one per exposure), and other oddities; nightly; 'anomaly' ranking candidates (also G3 shifts); `COMP_ANOMALIES` shadow | 042 |
| G5 | `recommend/fair.py`: decision-time state on each action, within-state (CEM) followed-vs-not with intervals; promotion uses it; 'follow_through' ranking candidates; `/api/advice-grades` | 043 |
| B4 | 'lagged' hypotheses in `stats/bayes.py`: exponential-decay lag sum, tau by evidence on identical rows, `effect_estimate.lag_profile`; hypothesis unique key now includes kind | 044 |
| S13 plants | interaction, schedule-change change point, heat effect on run HR with synthetic home weather, groin tweaks (own streams); tests set the demo home point | – |
| S13 eval | `healthtracker evaluate` (`sample/evaluate.py`), runtime rows and budgets (`pipeline/runner.py`), weekly shadow log (`stats/shadow_log.py`, Coach history card); G2 empty-series fix | 045 |
| H3 fix | no missed-dose finding from fewer than 8 missed days (found by the evaluation) | 046 |
| F2/B6 tweaks | `stats/tweaks.py`: groin-tweak Bayesian logistic screen (Laplace, strong priors), exploratory, `/api/tweak-screen` | 047 |
| Docs | README (parts, roadmap, CLI, privacy), design notes (fair grading, B1-B6, the plan, shadow first, medication apart, forecasts and change points, evaluation), `docs/evaluation.md` | – |

## Next, in order

Everything in the brief's "Next" list is built. What remains is gated on data or the season:

1. **In-season tapering before tournaments** (F1 table): a lighter week and no hard sessions in the
   three days before a multi-game day.
2. **D2 with fitted time constants** once 4-6 months of runs exist (a tau grid by evidence, like B4).
3. **G1 v2** (about 90 days): learned readiness weights trained on next-session outcomes; must beat
   v1 on held-out days before it replaces it.
4. **C4's interaction definition**: "hard day" (load z >= 1) is too strict to ever confirm the
   planted interaction in 120 demo days (see docs/evaluation.md).
5. Switch shadow components on by the phasing table (A2 bands and E3 about Dec 1; C1-C4, G1 v1,
   F4 on Jan 4; F7, D5, G3 on Feb 8; G2 in spring), each after a look at the shadow log.

## Judgment calls to raise with the owner

- **VO2 build spacing.** The brief puts the two VO2 sessions on Wed and Thu, which breaks "no back-to-back hard days". The template uses Tue and Thu. Default spacing also lets a hard endurance day follow a sprint/strength day (otherwise only two hard days fit in Mon-Thu); `ANALYSIS_PLAN.strict_spacing = 1` forbids any adjacent hard days.
- **Safety templates live.** The dizziness-in-training and lowest-mood (988) medication messages are live (`COMP_MED_SAFETY`), alongside the adherence display; the readiness template is in shadow.
- **Journal**: mood and energy are among the journal taps, which answers the brief's open question for H4.
- **Medication never feeds coaching rules.** F4 ignores the medication readiness flag (it acts only through its own template); dose time is analyzed only in H3, not in E3.
- **Screen prior 0.2 SD** instead of the brief's 0.1 example: 0.1 hid a planted per-drink effect in 90 demo days.
- **F5 progression needs a graded plan.** With no plan (or no key sessions) last week the load holds at 1.0, so the very first generated week holds. `progress_key_pct` (default 100) sets how many key sessions must be done.
- **Deload weeks** count weeks within each phase (base Nov 30, VO2 build Jan 25, pre-season Mar 1 and Mar 29 with the defaults). Key sessions keep their low-end duration (intensity kept); everything else shrinks by the deload factor (0.65), which gives about 30% less load than a normal week. The WHO lengthening is skipped in deloads, on purpose.
- **F7 holds back max-velocity sprints in pre-season weeks 1-2** even though base and VO2 build include them every other week (the brief's wording). If that feels like a step back, `return_maxv_week = 2` shortens it. Depth jumps are not staged (the jump ramp cap governs them).
- **F6 SRI threshold 70** is a starting point, not a clinical cutoff (`ANALYSIS_PLAN.sri_low`).
- **G2 on the demo:** weight and VO2 max forecasts beat naive; aerobic pace (trend and D2) does not yet, so they stay hidden even once live.
- **G4/G5/B4** are in shadow with the ranking. G5 changed rule promotion itself (matched instead of raw comparison; the proposals still need approval).
- **H3 minimum of 8 missed days** for a missed-dose finding: stricter than before (it only gated the travel template). Wording unchanged.
- **Tests now set HOME_LAT/HOME_LON** to the demo's synthetic home point so demo sessions get the synthetic weather (needed once the heat effect was planted).
- **G3 reports shifts about three weeks late** (it waits 20 measurements to be sure). Dose-change links are stored in the sensitive table but shown nowhere: showing them needs a new approved wording (a ninth template), which is the owner's call.

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
- `planning/progression.py` holds phase-week counting, deload scheduling, return-to-ultimate stages, and lift steps; `DayState.return_stage` carries the stage into the library's `check()`.
- `medication.SENSITIVE_TABLES` must list every table commented 'sensitive' (a test checks).

## Testing on this machine

No Docker or system Postgres. A self-contained Postgres 16 (from the `pgserver` pip package, in a scratch venv) runs on `127.0.0.1:55432`, TCP only:

```bash
BIN=<scratch>/pgvenv/lib/python3.12/site-packages/pgserver/pginstall/bin
$BIN/initdb -D <scratch>/pgdata -U health --auth=trust -E UTF8 --locale=C.UTF-8   # first time only
$BIN/pg_ctl -D <scratch>/pgdata -o "-p 55432 -c unix_socket_directories='' -c listen_addresses=127.0.0.1 -c fsync=off" -l <scratch>/pg.log start
export TEST_DATABASE_URL=postgresql://health@127.0.0.1:55432/healthtracker_test
.venv/bin/pytest -q                  # ~342 tests, about two minutes
cd web && npx tsc -b                 # typecheck the app
```

The scratch folder is per session, so a new session recreates the venv (`uv venv pgvenv && uv pip install --python pgvenv/bin/python pgserver`) and runs `initdb` again.
