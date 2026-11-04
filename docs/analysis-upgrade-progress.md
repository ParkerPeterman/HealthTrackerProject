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

## Next, in order

Finish the items the brief marks live for the week of Nov 9 first:

1. **F2** session library (`knowledge.session_library`, new exercises: Copenhagen levels, landmine press, band ER/IR, CMJ/broad/depth jumps, trap-bar deadlift…), physio-prompt rule, shoulder/groin/hamstring handling.
2. **F3** weekly plan generator (phase templates, Mon–Thu main days + optional Fri–Sun bonus slots, ramp caps from `planning/ramp.py`, spacing, injury restrictions, weather swaps once A7 exists, structured reason per session, plan versions). Replace the logic in `planning/weekly.py`; keep `test_weekly_plan_respects_games` passing. Add the guardrail tests: no plan exceeds a ramp cap; no hard session under a restriction.
3. **H1, H2, H5** medication check (generic tables `core.medication`, `core.medication_change`, `core.medication_log`; Today card Taken / Not yet, yesterday prompt with "Don't remember"; features in a separate sensitive `analytics.medication_daily`; dashboard opt-in; demo uses a made-up drug name). Wire `features/baselines.reset_dates` (already reads `core.medication_change` once it exists).
4. **H4 adherence display** with the approved templates (below), appended after the LLM guard; the LLM payload must never contain medication fields.
5. **A7** Open-Meteo weather (`raw.ingest` source 'open_meteo', `core.weather_hourly/daily`, HOME_LAT/HOME_LON in .env rounded to 0.1°, attribution text, README Privacy update). Then the **D3 aerobic-fitness trend** (pace at ref HR with weather covariates, statsmodels local linear trend, decoupling threshold 5% as a parameter, smoothed VO2 max).
6. **E1** sleep regularity (Phillips SRI, midpoint SD, weekday/weekend gap, 7/28-day); **E2** trust order (`knowledge.feature_quality`, journal `sleep_quality` replacing motivation in the UI, no stage-only insights, poor-tier rules can't be activated alone); **C5** structured insight records + confidence words + guard.

Then the shadow items and later phases: B1–B7, C1–C4, D2, D5, E3–E5, F4–F7, G1–G5, H3, Section 13 evaluation (generator plants, calibration, backtests, shadow-comparison log, insight "new to me / knew it" rating, runtime check), README and design-notes updates.

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
.venv/bin/pytest -q                  # ~140 tests, about a minute
cd web && npx tsc -b                 # typecheck the app
```

The scratch folder is per session, so a new session recreates the venv (`uv venv pgvenv && uv pip install --python pgvenv/bin/python pgserver`) and runs `initdb` again.
