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
a shadow rule when graded outcomes are better on followed days (Welch t-test). It proposes retiring
an active non-safety rule when following it is significantly worse. A personally validated rule
(the caffeine cutoff) also needs a supported insight. All proposals wait for approval.

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

## When is a finding "supported"?

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
