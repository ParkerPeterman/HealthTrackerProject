"""B5: partial pooling cuts false positives in a family of mostly-null effects and keeps a real one."""

import numpy as np
import pytest

from healthtracker.stats.pooling import family_of, p_beyond, pool


def test_pooling_controls_false_positives():
    rng = np.random.default_rng(3)
    false_raw = false_pooled = 0
    real_kept = 0
    sims = 300
    for _ in range(sims):
        truth = np.r_[np.zeros(9), 0.8]
        se = np.full(10, 0.2)
        est = truth + rng.normal(0, se)
        mean, sd, _, _ = pool(est, se)
        raw_p = p_beyond(est, se, 0.2)
        pooled_p = p_beyond(mean, sd, 0.2)
        false_raw += int((raw_p[:9] >= 0.8).sum())
        false_pooled += int((pooled_p[:9] >= 0.8).sum())
        real_kept += int(pooled_p[9] >= 0.8)
    assert false_pooled < false_raw
    assert real_kept / sims > 0.8


def test_identical_effects_pool_tightly():
    mean, sd, mu, tau = pool(np.r_[0.30, 0.31, 0.29, 0.30], np.full(4, 0.15))
    assert mu == pytest.approx(0.3, abs=0.01) and np.all(sd < 0.15)


def test_families():
    assert family_of("alcohol_drinks", "sleep_score") == "evening->sleep"
    assert family_of("had_game", "hrv") == "load->recovery"
    assert family_of("med_missed", "hrv") is None


@pytest.mark.db
def test_pooled_estimates_in_the_weekly_run(seeded, tx):
    from healthtracker.db import fetch_all
    from healthtracker.stats.bayes import run_effects

    run_effects(tx, seeded["end"])
    rows = fetch_all(tx, "SELECT * FROM analytics.effect_estimate WHERE computed_on = %s AND family IS NOT NULL",
                     (seeded["end"],))
    pooled = [r for r in rows if r["pooled_sd"] is not None]
    assert pooled and all(r["se_sd"] > 0 for r in pooled)
