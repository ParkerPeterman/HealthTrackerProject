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

## Next, in order

Everything the brief marks live for the week of Nov 9 is done. Next, the shadow items:

1. **B1-B3**: conjugate Normal-Inverse-Gamma effect estimates after AR(1) prewhitening (P(|b| > SWC), 80/95% intervals, days to reach 0.8), `knowledge.effect_priors` with cited sources (Normal(0, 0.5 SD) where none), continuous doses. Swap C5's direction probability for P(meaningful) once B1 runs.
2. **G1 v1** readiness score (0-100, weighted standardized deviations, top drivers; shadow).
3. **F4** daily adjustment (keep / downgrade / swap / rest from A2 band, RHR, sleep, journal, H4 flag; shadow), reading the H4 readiness flag.
4. **H3** medication analysis (weekly job, shadow): dose change = known change point, missed-dose B1 estimates with weak priors, dose time vs sleep, travel confounding, templates 6-8.

Then B4-B7, C1-C4, D2, D5, E3-E5, F5-F7, G2-G5, Section 13 evaluation, README and design-notes updates.

## Judgment calls to raise with the owner

- **VO2 build spacing.** The brief puts the two VO2 sessions on Wed and Thu, which breaks "no back-to-back hard days". The template uses Tue and Thu. Default spacing also lets a hard endurance day follow a sprint/strength day (otherwise only two hard days fit in Mon-Thu); `ANALYSIS_PLAN.strict_spacing = 1` forbids any adjacent hard days.
- **Safety templates live.** The dizziness-in-training and lowest-mood (988) medication messages are live (`COMP_MED_SAFETY`), alongside the adherence display; the readiness template is in shadow.
- **Journal**: mood and energy are among the journal taps, which answers the brief's open question for H4.

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
.venv/bin/pytest -q                  # ~220 tests, about a minute
cd web && npx tsc -b                 # typecheck the app
```

The scratch folder is per session, so a new session recreates the venv (`uv venv pgvenv && uv pip install --python pgvenv/bin/python pgserver`) and runs `initdb` again.
