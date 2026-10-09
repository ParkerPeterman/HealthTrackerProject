from healthtracker.llm.guard import check_output
from healthtracker.llm.phrase import render, template_sections

PAYLOAD = {
    "date": "2026-10-08",
    "status": {"recovery": {"hrv_ms": 48, "hrv_vs_baseline_sd": -1.4, "sleep_last_night": "6h45"}},
    "actions": [{"id": 11, "text": "Keep today easy: nothing above RPE 6, since you had a game yesterday"},
                {"id": 12, "text": "Eat at least 140 g of protein today"}],
}
ACTIONS = [{"id": 11, "action_text": PAYLOAD["actions"][0]["text"], "target_value": 6, "target_value_high": None},
           {"id": 12, "action_text": PAYLOAD["actions"][1]["text"], "target_value": 140, "target_value_high": None}]


def good():
    return {"summary": "HRV is 48 ms, 1.4 SD below your baseline, after 6h45 of sleep.",
            "items": [{"action_id": 11, "message": "Go easy today and stay at RPE 6 or below after yesterday's game."},
                      {"action_id": 12, "message": "Get at least 140 g of protein today."}]}


def test_good_output_passes():
    assert check_output(good(), PAYLOAD, ACTIONS).passed


def test_invented_number_fails():
    out = good()
    out["items"][1]["message"] = "Get at least 140 g of protein, about 35 g per meal."
    res = check_output(out, PAYLOAD, ACTIONS)
    assert not res.passed and "35" in res.problems[0]


def test_missing_and_extra_actions_fail():
    out = good()
    out["items"] = out["items"][:1] + [{"action_id": 99, "message": "Drink water"}]
    problems = " ".join(check_output(out, PAYLOAD, ACTIONS).problems)
    assert "missing actions [12]" in problems and "not made by the rules [99]" in problems


def test_lost_target_fails():
    out = good()
    out["items"][0]["message"] = "Keep it light today after the game."
    assert any("lost its target" in p for p in check_output(out, PAYLOAD, ACTIONS).problems)


def test_advice_in_summary_fails():
    out = good()
    out["summary"] = "HRV is 48 ms, so you should rest."
    assert any("summary gives advice" in p for p in check_output(out, PAYLOAD, ACTIONS).problems)


def test_rounding_of_input_numbers_is_allowed():
    out = good()
    out["summary"] = "HRV is 48 ms, about 1 SD below normal."
    assert check_output(out, PAYLOAD, ACTIONS).passed


def test_schema_mismatch_fails():
    assert not check_output({"text": "hi"}, PAYLOAD, ACTIONS).passed


def test_template_render_is_markdown_with_sections():
    payloads = {"training": {**PAYLOAD, "actions": PAYLOAD["actions"][:1]},
                "nutrition": {"status": {"nutrition_yesterday": {"kcal": 2400, "protein_g": 120}},
                              "actions": PAYLOAD["actions"][1:]}}
    text = render(template_sections(payloads), payloads)
    assert text.startswith("**Training & recovery**\n\nRecovery: HRV 48 ms (-1.4 SD vs baseline)")
    assert "- Eat at least 140 g of protein today" in text and "Yesterday: 2400 kcal, 120 g protein." in text
