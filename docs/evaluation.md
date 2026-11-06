# Evaluation: the demo athlete as a test bench

Brief section 13. `healthtracker evaluate` builds a synthetic athlete of each length (same seed
and end date) in a scratch database, runs the full backfill plus one nightly and one weekly run,
and reads every method back against what the generator planted. Nothing here is real data. The
run below was on 2026-11-05 (after D2's fitted time constants, G1 v2, and the C4 change), on the
8-core / 6 GB dev machine.

P = P(|effect| > SWC); a planted effect should rise and keep its sign, the others stay low.

| | 14 days | 30 days | 60 days | 120 days |
|---|---|---|---|---|
| caffeine from 14:00 -> sleep score (planted) | too early | P 0.91 (-4.77) ✓ | P 0.84 (-4.78) ✓ | P 1.00 (-6.32) ✓ |
| caffeine from 14:00 -> sleep (planted) | too early | P 0.40 (-3.62) ✓ | P 0.76 (-12.15) ✓ | P 0.78 (-10.12) ✓ |
| each drink -> HRV (planted) | too early | P 0.70 (-1.68) ✓ | P 0.77 (-1.75) ✓ | P 1.00 (-3.08) ✓ |
| each drink -> resting HR (planted) | too early | P 0.71 (+0.52) ✓ | P 0.99 (+0.82) ✓ | P 1.00 (+1.35) ✓ |
| late meal -> sleep score (planted) | too early | P 0.77 (-3.5) ✓ | P 0.51 (-0.95) ✓ | P 1.00 (-5.9) ✓ |
| game -> HRV next morning (planted) | too early | P 0.84 (-3.69) ✓ | P 0.69 (-2.16) ✓ | P 0.84 (-3.4) ✓ |
| short night -> energy (planted) | too early | too early | too early | P 0.67 (-0.18) ✓ |
| fiber 30 g+ -> sleep score | too early | P 0.65 (+2.4) | P 0.62 (+1.88) | P 0.56 (+0.73) |
| low protein -> soreness | too early | too early | too early | P 0.44 (+0.02) |
| low carbs -> energy | too early | too early | too early | too early |
| under 2000 kcal -> energy | too early | too early | too early | too early |
| B4 game -> HRV lags | – | tau 0.5 (unclear): -3.1, -0.4 | tau 0 (unclear): -2.1 | tau 4 (unclear): -4.5, -3.5, -2.8, -2.1 |
| C4 hard day after short night | too few days | too few days | too few days | -3.1 ms (P 0.77) |
| D3 pace trend, s/km a week | – | – | -2.11 (hi80 -1.87) | -0.97 (hi80 -0.82) |
| D3 temp / humid, s/km per °C | – | – | +0.27 / +0.17 | +0.20 / +0.20 |
| G2 beats naive | none | none | weight_kg | aerobic_pace (D2), vo2max |
| G3 shifts | none | none | weight_kg Jun 06 -0.5 | weight_kg Apr 12 -1.2; aerobic_pace May 18 -9.0; resting_hr May 21 -1.7; sleep_midpoint May 27 +43.8 |
| G4 unusual days (explained) | 0 (0) | 14 (12) | 14 (13) | 34 (24) |
| G1 v2 vs v1 (MAE, P better) | too few sessions | too few sessions | no held-out days yet; top resting_hr x3.33, sleep x1.24 | 0.53 vs 0.53, P 0.58; top sleep x3.8, resting_hr x1.39 |
| H3 medication (max P) | 11 estimates, max P 0.00 | 11 estimates, max P 0.43 | 11 estimates, max P 0.81 | 11 estimates, max P 0.82 |
| runtime: backfill / nightly / weekly | 3 s / 3 s / 2 s | 14 s / 9 s / 7 s | 37 s / 11 s / 9 s | 89 s / 15 s / 12 s |

Budgets: nightly about 2 minutes, weekly about 10 (brief section 13).

## What it shows

- **B1 works as intended.** No planted effect ever has the wrong sign. Most reach P(meaningful)
  0.8 or more by 120 days, and nothing is claimed at 14 days. The unplanted exposures stay below
  0.7, and the strongest of them (fiber) fades as days are added. Estimates move between 30 and
  60 days as the demo's travel, illness, and overreaching blocks enter the data, which is why the
  gates are in days, not just in P.
- **B4 doesn't settle the shape of the game effect** in 120 days. It marks it unclear (the best
  decay holds under half the evidence weight) rather than claiming one. The demo's HRV also
  carries day-to-day persistence (AR 0.75), which smears a two-morning effect.
- **C4's interaction never got its 4 qualifying days** while its "hard day" meant load z of 1
  or more, stricter than the planted one (any game or tempo run). A hard day is now a game or a
  session at RPE 7 or more (the app's own definition) or a big load day. At 120 days: 7
  qualifying days, -3.1 ms beyond the two added up (P 0.77, "possibly"; planted about -6 ms,
  diluted by heavy lifts that also count as hard).
- **G1 v2 learns the planted direction but doesn't replace v1.** The demo plants session feel
  on sleep and HRV; v2 puts the most weight on sleep by 120 days and drops components that
  would need a negative weight. It predicts held-out sessions no better than v1 (MAE 0.53 vs
  0.53), so v1 stays. The plant is in one of three outcome parts, and readiness predicts single
  sessions only weakly, so this is the expected result at four months.
- **D2's fitted time constants don't come into play here.** Their gate is about five months of
  runs (150 days), past the longest demo; with the gate lowered on 90 days the evidence over the
  grid is flat and 42/7 stay.
- **D3 needs weather to see fitness through the heat.** With the synthetic weather, its
  temperature coefficient is positive from 60 days and the fitness trend is clear. Without
  weather, the planted heat effect hid the trend, because spring warming correlates with the
  date at about 0.8. Over a full seasonal cycle the two separate; in one spring they don't fully.
- **G2 shows forecasts only where they beat naive.** Weight beat naive at 60 days but not at
  120 (the demo's steady loss is close to naive over two weeks); VO2 max and D2's pace forecast
  under the plan beat it at 120.
- **G3 finds the planted schedule change** one day off at 120 days. It also reads the
  overreaching block as an HRV/resting-HR shift (it is one) and the demo's steady weight loss
  as steps, a known limit of a constant-mean model on a trend.
- **G4 explains most unusual days** (24 of 34 at 120 days) from the record.
- **H3 (nothing planted) shows the risk of tiny samples.** The raw estimates reach P 0.82 at
  60-120 days, from two or three missed days that fall right after the demo's trip. Since commit
  376fbe8 no missed-dose finding is made from fewer than 8 missed days, so the message stays
  "too early to tell".
- **Runtime is far inside budget:** a 120-day athlete takes 18 s nightly and 15 s weekly against
  budgets of about 2 and 10 minutes. Every run now records its seconds (`pipeline_run`, step
  `runtime`).

Re-run with `healthtracker evaluate [--days 14,30,60,120] [--out FILE]` (the file gets the table only). It needs
`DEMO_DATABASE_URL`; the scratch databases are `<demo>_eval_<days>` and are dropped afterwards.
