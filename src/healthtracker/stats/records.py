"""C5: structured insight records and the fixed confidence vocabulary.

Every insight is stored as a record: claim type, exposure, outcome, effect with units, its
interval, a probability and the confidence word for it, the data window, and what would
change it. The sentence shown is rendered from the record by code. The LLM may restate a
record, but its output guard rejects any number or confidence word that isn't in it.

Until the Bayesian estimates (B1) exist, the probability is the probability that the
effect's direction is right, from the HAC estimate under a flat prior: P = Phi(|b| / SE).
B1 replaces it with P(|b| > the smallest worthwhile change).
"""

from __future__ import annotations

import math

from healthtracker.stats.hypothesis import UNITS, Hypothesis, TestResult, describe

# The vocabulary is fixed here and nowhere else (brief C5).
CONFIDENCE = ((0.95, "very likely"), (0.8, "likely"), (0.6, "possibly"), (0.0, "too early to tell"))
WORDS = tuple(w for _, w in CONFIDENCE)
# Words that claim more certainty than any record can carry; never allowed in phrased output.
OVERCLAIM = ("definitely", "certainly", "clearly", "proven", "proves", "guaranteed", "almost certainly", "probably",
             "unlikely", "strongly suggests", "conclusive")


def confidence_word(p: float | None) -> str:
    if p is None or math.isnan(p):
        return "too early to tell"
    return next(w for t, w in CONFIDENCE if p >= t)


def direction_probability(r: TestResult) -> float | None:
    if r.effect is None or r.ci_low is None or r.ci_high is None:
        return None
    se = (r.ci_high - r.ci_low) / (2 * 1.96)
    if se <= 0:
        return None
    return round(0.5 * (1 + math.erf(abs(r.effect) / se / math.sqrt(2))), 3)


def build_record(r: TestResult, h: Hypothesis, quality_problem: str | None = None) -> dict:
    exposure, outcome = describe(h)
    p = direction_probability(r)
    word = confidence_word(p) if r.p_value is not None else "too early to tell"
    n_days = r.n_exposed + r.n_unexposed
    if r.p_value is None:
        need = max(0, h.min_n_per_group - min(r.n_exposed, r.n_unexposed))
        change = f"about {need} more days in the smaller group ({r.n_exposed} with, {r.n_unexposed} without so far)"
    elif word in ("likely", "very likely"):
        change = "the effect shrinking or reversing as more days come in"
    else:
        change = "more days with and without the exposure; the interval still includes small or no effect"
    if quality_problem:
        change = f"a better measure: this {quality_problem}"
    return {
        "claim_type": "effect", "exposure": exposure, "outcome": outcome, "exposure_expr": h.exposure,
        "outcome_col": h.outcome, "lag_days": h.lag_days,
        "effect": None if r.effect is None else round(r.effect, 1), "unit": UNITS.get(h.outcome, "points"),
        "interval": None if r.ci_low is None else [round(r.ci_low, 1), round(r.ci_high, 1)], "interval_level": 95,
        "probability": p, "probability_kind": "direction", "confidence": word,
        "window": [str(r.window[0]), str(r.window[1])] if r.window else None, "n_days": n_days,
        "n_exposed": r.n_exposed, "n_unexposed": r.n_unexposed, "what_would_change": change,
        "shown": quality_problem is None,
    }


def render(rec: dict) -> str:
    """The one sentence for a record, from its fields only."""
    if rec["effect"] is None:
        return (f"Too early to tell whether {rec['exposure']} changes {rec['outcome']}: "
                f"{rec['n_exposed']} days with, {rec['n_unexposed']} without. What would change this: "
                f"{rec['what_would_change']}.")
    direction = "higher" if rec["effect"] > 0 else "lower"
    unit = f" {rec['unit']}" if rec["unit"] else ""
    lo, hi = rec["interval"]
    return (f"{rec['confidence'].capitalize()}: after {rec['exposure']}, {rec['outcome']} is "
            f"{abs(rec['effect']):g}{unit} {direction} ({rec['interval_level']}% interval {lo:g} to {hi:g}; "
            f"{rec['n_exposed']} vs {rec['n_unexposed']} days, {rec['window'][0]} to {rec['window'][1]}). "
            f"What would change this: {rec['what_would_change']}.")
