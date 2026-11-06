# Design notes

Short notes on the decisions that shape the system, including the places where the build had to
pick something the plan left open.

## Code decides, the LLM phrases

Every number, flag, and target comes from SQL and Python. The LLM gets a compact JSON payload
(status in display units, fired rules, the actions with ids) and must return
`{"summary", "items": [{"action_id", "message"}]}` under a JSON schema. `llm/guard.py` then checks
that:

- exactly the rule-made actions are covered, once each;
- every number in the text appears in the input (roundings allowed);
- each action's target number survives the rewording;
- the summary only describes and gives no advice of its own.

On any failure (after one retry) or if Ollama is down, a deterministic template is used and the
reason is stored in `recommendation.llm_check`. There are two small calls (training/recovery and
nutrition) because 7-14B models do better with one task per call.

## Every suggestion is checkable

A rule emits `Action(metric, comparator, target, evaluate_on, outcome_metric)`. Each morning the
grading job resolves the metric for that day (`recommend/metrics.py`): a `daily_features` column or
a parameterized metric such as `hard_sets_day:quads` or `activity_rpe:cutting`. It records whether
the action was followed and how the outcome (for example next-morning HRV z) compared to baseline.
Positive `outcome_delta` always means better. Overnight metrics count as known on the morning of
their own date; everything else is known the morning after.

Shadow rules produce hidden actions that are graded the same way. The weekly job proposes promoting
a shadow rule when graded outcomes are better on followed days, and retiring an active non-safety
rule when following it is significantly worse. A personally validated rule (the caffeine cutoff)
also needs a supported insight. All proposals wait for approval.

The comparison is fair (G5, `recommend/fair.py`). Advice gets skipped on bad days, and bad days
have worse outcomes anyway, so a raw followed-vs-not difference flatters every rule. Each action
logs a coarse decision-time state: HRV band, a night under 7 h, a travel or game day. Followed and
not-followed days are compared only within the same state, weighted by the followed days, with
intervals. The raw difference is kept beside it so the bias is visible. In a simulation where
advice is skipped mostly on bad days, the raw difference is twice the true effect and the matched
one recovers it.

When two visible actions constrain the same metric on the same day ("nothing above RPE 6" and
"RPE 4 or less"), only the stricter one is shown. Both are still stored and graded.

## Baselines over eligible days, not calendar days

Sick, travel, and injury days are excluded from baselines. With a calendar window, a three-week
injury blanked every z-score for a month. That is exactly when recovery monitoring matters. Baselines
therefore use the last 30 *eligible* observations, within 90 days. The 7-day HRV line is a display
trend, so it skips only sick and travel days.

## Data quality

- **Games without the watch** are logged manually. A manual game makes the day partial, so
  all-day metrics (stress, Body Battery) are nulled and never trigger rules.
- **Partial days** are also detected from all-day HR coverage (under 20 h is partial; configurable).
- **Overlapping workouts:** when a manual entry overlaps a Garmin activity by at least half the
  shorter one, the manual row is superseded. The Garmin row inherits what only the manual entry
  knew: lift sets, the game flag, RPE when Garmin has none, and notes. Lifts logged in the app are
  not lost when the watch also recorded the session.
- **Missing food days** are stored as missing, never zero. Nutrition features exist only for days
  marked complete, and weekly rules require a minimum number of logged days.
- **Effort scale:** Garmin's `directWorkoutRpe` is treated as 10-100 in steps of 10. This needs
  checking against real payloads (plan open question #2). Raw payloads are kept, so a parser fix
  plus `healthtracker backfill` corrects history.
- **Missing RPE:** session load is RPE × minutes. When a session has no RPE, it is imputed from
  %HRmax (observed max or Tanaka 208 − 0.7·age).

## Effects: Bayesian estimates, with the old test as a cross-check

B1 (`stats/bayes.py`, shadow until `COMP_BAYES_EFFECTS` is on): outcome = a + b·exposure at the
hypothesis's lag + controls, with AR(1) prewhitening and a conjugate Normal-Inverse-Gamma update.
That makes it exact, fast, and deterministic. It reports the median, 80% and 95% intervals,
P(direction), P(|b| > SWC), and the days until that would reach 0.8.

- **Priors (B2):** every prior in `knowledge.effect_priors` cites its source, with directions
  from published research and wide SDs. Without a source the prior is Normal(0, 0.5 SD). No
  invented numbers.
- **Doses (B3):** drinks, caffeine mg, and minutes enter as numbers, not yes/no groups.
- **Lagged effects (B4):** a 'lagged' hypothesis spreads its exposure over the first lag and the
  three days before it as one exponentially decaying sum. The decay comes from a small grid by
  model evidence, fitted on identical rows (one AR(1) rho for all) so the evidences compare.
  When no decay wins at least half the weight, the text says how long it lasts isn't clear yet.
- **Families (B5):** estimates in a family (evening behaviors -> sleep, load -> recovery) are
  pooled toward the family mean instead of a Benjamini-Hochberg cut.
- **The screen (B6):** every exposure x outcome x lag weekly under a strong Normal(0, 0.2 SD)
  prior (0.1 hid a planted per-drink effect in 90 demo days). Groin tweaks, being rare, get a
  Bayesian logistic model with Normal(0, 0.5) priors on log-odds. All of it is exploratory: a
  candidate for an experiment, never a rule.

## When is a finding "supported"? (the OLS cross-check)

For each pre-registered hypothesis (`exposure` expression, `outcome` column, `lag_days`):

- OLS of the outcome on the exposure, controlling for day of week, prior night's sleep, and the
  previous day's training load. A control is dropped when it is the exposure itself or on its
  causal path; for overnight outcomes the "prior night" is the night before the one measured.
- Newey-West (HAC) standard errors, plus a moving-block bootstrap (7-day blocks), because
  consecutive days aren't independent.
- Benjamini-Hochberg across everything tested in the run.
- **Supported** requires at least 20 days per group, adjusted p < 0.05, both CIs excluding zero,
  an effect of at least 0.2 SD, and the same direction (p < 0.10) in the later half of the data.

Self-experiments assign days in randomized pairs (balanced across weekdays). They are analyzed
intention-to-treat with a permutation test, and adherence is reported from the graded protocol
actions.

## Parameter tuning

Tuners propose values within each parameter's `[min_allowed, max_allowed]`. Proposals and
decisions are logged in `rule_parameter_history`.

- **Caffeine cutoff:** the hour h where caffeine at or after h hurts that night's sleep score
  (vs. days with none after noon), caffeine in [12:00, h) doesn't, and the before/after contrast
  is largest.
- **Short-sleep minimum:** the threshold that best separates next-day energy.
- **Post-game recovery days:** how many days HRV stays clearly below baseline after games.

## Weight goals set the calorie target

A calorie number goes stale as training load and body weight change, so a weight goal drives the
target instead. Expenditure comes from my own data: average intake on complete food-log days
minus the energy stored or lost, read off the least-squares slope of the smoothed weight over 28
days at ~7,700 kcal/kg. It needs 14 complete days and 10 weigh-ins; until then a Mifflin-St Jeor
estimate (with an activity factor from recent training minutes) is used for the goal screen only.
The weekly job never proposes from the formula.

Target = expenditure + planned rate × 7,700 / 7, rounded to 50 kcal, ±100. Safety caps: loss at
most 1% of body weight per week, gain at most 0.5%, deficit at most 1,000 kcal/day, and a floor of
1,500 (male) / 1,200 kcal. Protein is 2.0 g/kg while losing and 1.6 g/kg otherwise (ISSN position
stand). Setting targets from the goal screen is an explicit choice; afterwards the weekly
`goal_check` writes `analytics.target_proposal` rows when the plan moves by 100 kcal or 0.1 g/kg,
and nothing changes until I approve. Progress is judged on the smoothed trend against the planned
path, with a tolerance of one week's planned change (at least 0.45 kg), so one heavy morning never
reads as "behind".

## The weekly plan

Code builds every session, day, and size (`planning/`); the LLM never touches it.

- **Spacing.** Two hard endurance days, or two hard neuromuscular days (sprints, jumps, heavy
  legs), are never back to back, and sprints never follow a hard endurance day. A hard endurance
  day may follow a sprint day; otherwise only two hard days fit in Monday to Thursday.
  `ANALYSIS_PLAN.strict_spacing = 1` keeps every hard day apart. Quality work lands at least 48
  hours before a game, or as long as the session's learned recovery cost needs (D5).
- **Ramp caps.** Running load, sprint metres, and jump contacts are capped by the same guards the
  rules flag (`planning/ramp.py`). Optional sessions shrink or drop first.
- **Progression (F5).** The load target rises only after a graded week whose key sessions were
  done and whose recovery held; otherwise it holds. Every 4th week of a training phase is a
  deload: key sessions stay at the low end of their range (intensity kept), the rest shrink by
  the deload factor (about 30% less load), and lifts keep their weight with one set fewer. Lift
  suggestions follow the RIR of the last session.
- **Health in the plan (F6).** WHO minutes and strength days are counted from the planned
  sessions. Easy runs lengthen toward 150 minutes inside the caps, but not in a deload or after
  recovery flags. A low SRI gets a note, and the protein target is stated in grams.
- **Return to ultimate (F7).** In pre-season, weeks 1-2 are accelerations only, weeks 3-4 add
  max-velocity sprinting and cutting, and week 5 on adds game-like work. Each library session
  names its first stage, and sprint metres scale 0.5 / 0.75 / 1.0 before the caps. Injury
  restrictions apply on top, everywhere.

## Shadow first

New logic runs hidden before it is shown. Rules have `status = shadow`. Non-rule components
have `COMP_*` switch rows checked with `components.is_live()`, with initial statuses from the
brief's phasing table:
- forecasts (`COMP_FORECASTS`) and the readiness score: in the season
- change points (`COMP_CHANGE_POINTS`): in pre-season
- ranked insights and anomalies: from Jan 4

The weekly shadow log (`stats/shadow_log.py`, on the Coach history tab) shows where old and new
logic disagreed: HRV z vs the band, the two illness checks, the daily adjustment on planned hard
days, readiness vs the old flags, learned vs equal readiness weights, OLS vs B1, and raw vs fair
grading. That is the evidence for switching something on; the log changes nothing itself.
`healthtracker components` puts the two together: each switch's planned date from the brief's
phasing table, read against my own season (a later first game moves the pre-season parts with
it), its data gate, and the latest shadow-log line. It marks a switch DUE and leaves the
switching to me.

## Readiness: equal weights until learned ones earn it

Readiness v1 (G1, `features/readiness.py`) weighs its components equally, the robust default
with no published weighting. v2 (`features/readiness_v2.py`) learns the weights each week from
how that day's sessions went: effort against the plan (or my usual RPE for the type), pace at
the reference HR against D3's trend, and my rating. The priors sit at v1's weights, so with
little data v2 stays close to v1. Each past day is scored by the model fitted before it, never
after. v2 replaces v1 only when a rolling-origin backtest shows it predicting held-out sessions
clearly better: a 2% lower mean error and P >= 0.95 paired by day. v1 is calibrated on the same
training days, so the comparison is fair. In simulation it swapped in at most 7% of histories
when nothing mattered or equal weights were right. When two components carried the signal it
swapped in a sixth of histories at three months and about half at eight. Readiness predicts
single sessions only weakly, so it takes months. No weight may be negative, because each
component is signed so that higher means more ready: on the demo an unconstrained fit said
soreness helped. Even an adopted v2 is shown only once `COMP_READINESS_V2` is on. The
medication flag is not learned: it keeps v1's fixed weight relative to the learned ones, and
flagged days are left out of the learning.

## Medication stays apart

Medication tables are sensitive (`COMMENT ... 'sensitive'`, listed in
`medication.SENSITIVE_TABLES`; a test keeps the two in step). They never reach
`daily_features`, the recommendation snapshot, or the LLM prompt.
- **Messages:** every one comes from an approved template, filled by code and added after the
  LLM guard. The guard also rejects medication, painkiller, and extra-water wording in LLM text.
- **Never a lever:** medication is never an experiment, never ranked as something to change,
  and never an input to coaching rules (F4 ignores the readiness flag). Readiness v2 doesn't
  learn a weight for it either.
- **Findings (H3):** a missed-dose finding needs at least 8 missed days behind it. The Section
  13 evaluation found a spurious "likely" from two.
- **Shifts (G3):** a dose change near a shift is linked only in its own sensitive table, and is
  shown nowhere until a wording is approved.

## Forecasts and change points

Forecasts (G2, `stats/forecast.py`) use the same local-linear-trend model as D3 and E4. Each is
backtested from rolling origins against a naive forecast (the last value; for weight, the last
week's mean) and is shown only once it beats naive. Goal arrival dates are ranges (most likely,
earliest, latest at 80%), or "not within a year".

The fitness-fatigue model (D2, `features/fitness_fatigue.py`) starts with time constants of 42
and 7 days. Once runs span about five months (`ANALYSIS_D2.fit_taus_days`, `fit_taus_runs`), it
scores a grid of pairs (fitness 21-60 days, fatigue 3-14) by model evidence plus log-normal
priors centred on 42 and 7. It keeps the best pair only when that pair forecasts clearly better
in a backtest that re-picks the pair from the runs before each origin: a mean error at least 5%
lower, and P >= 0.95 run by run. In simulation, "any lower error" swapped the constants in most
histories whose true constants were 42/7. This rule did it in 8-15%, and still found constants
far from 42/7 (60/3) in 4 of 5 at the gate. The fatigue constant comes out within a grid step. The
fitness constant often doesn't, a known weakness of the Banister model, so the model compares
plans and is never ground truth.

Change points (G3, `stats/changepoint.py`) are Bayesian online change-point detection. The daily
series are prewhitened with their own AR(1) first: on autocorrelated noise without prewhitening,
it found 19 to 84 false shifts per 40 series. A shift is reported about three weeks after it
happens, with what else changed nearby (tags, phases, logs), as candidates, not causes.

## Evaluation (section 13)

`healthtracker evaluate` builds demo athletes of 14, 30, 60, and 120 days in scratch databases and
reports what each method finds against what was planted, plus run times. Results and caveats are
in [evaluation.md](evaluation.md). The demo plants each effect deterministically or on its own
random stream, so adding one never moves another. The tests set the demo's synthetic home point
so sessions get the synthetic weather.

## Scheduling

The plan calls the daily job "nightly", but last night's sleep only exists after the watch syncs
in the morning. It runs at 07:30 and again at 10:30 to catch a late sync, and from the app's
Refresh button. Every step is an upsert. Each step is logged to `pipeline_run`; a failing step
(e.g. Garmin down) doesn't stop the rest. Steps are plain functions, so Dagster or Prefect can
wrap them later without rewrites.

## Networking

The Python containers use host networking so they reach the host's Ollama on `127.0.0.1:11434`
and still bind only to localhost. Postgres publishes on `127.0.0.1` only. Remote access is
`tailscale serve`, which also provides the HTTPS the camera scanner needs.

## Units

Storage is metric. Display follows `profile.display_units` (imperial by default): lb, mi, and
min/mi in the app, dashboard, and coaching text. Protein stays in g/kg, the sports-nutrition
convention. The LLM receives display-unit numbers so it never converts anything.
