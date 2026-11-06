"""Paired comparisons of two forecasters' backtest errors on the same cases."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from scipy import stats


def p_smaller(a: Sequence[float], b: Sequence[float], min_n: int = 10) -> float:
    """One-sided P that errors `a` are smaller than errors `b` on average, paired case by case
    (a t test on the differences). 0 with fewer than `min_n` pairs or identical errors."""
    d = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    if len(d) < min_n or d.std(ddof=1) == 0:
        return 0.0
    t = d.mean() / (d.std(ddof=1) / np.sqrt(len(d)))
    return float(stats.t.cdf(-t, len(d) - 1))


def clearly_better(mae: float | None, mae_other: float | None, p: float, margin: float, p_min: float) -> bool:
    """A replacement rule: at least `margin` lower mean error and P >= p_min paired."""
    return mae is not None and bool(mae_other) and mae <= (1 - margin) * mae_other and p >= p_min
