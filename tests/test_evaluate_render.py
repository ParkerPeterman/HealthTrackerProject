"""Section 13: the evaluation report renders whatever each length produced, including nothing."""

from healthtracker.sample.evaluate import Result, render


def test_render_handles_empty_and_partial_results():
    short = Result(14)
    early = Result(30, readiness={"n": 31, "adopted": False, "gate": False, "n_backtest": 1, "mae_v1": None,
                                  "mae_v2": None, "p_better": 0.0, "top": [("sleep", 1.4), ("hrv", None)]})
    late = Result(120, readiness={"n": 90, "adopted": False, "gate": True, "mae_v1": 0.55, "mae_v2": 0.56,
                                  "p_better": 0.3, "top": [("energy", 3.8), ("sleep", 1.9)]})
    for r in (short, early, late):
        r.effects = []
    text = render([short, early, late])
    row = next(line for line in text.splitlines() if line.startswith("| G1 v2"))
    assert "too few sessions" in row and "no held-out days yet; top sleep x1.4" in row
    assert "0.56 vs 0.55, P 0.30; top energy x3.8, sleep x1.9" in row
