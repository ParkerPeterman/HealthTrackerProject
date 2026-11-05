"""Workstream H: the daily check, adherence features, approved templates, and privacy.

The brief's "done when" checks:
  * a blank day never counts as missed
  * medication never appears among experiment candidates
  * the LLM prompt never contains medication fields
  * a dose change resets baseline maturity
  * medication messages only ever come from templates
"""

import json
from datetime import date, timedelta

import pytest

from healthtracker.medication import is_sensitive
from healthtracker.medication.features import feature_rows
from healthtracker.medication.messages import TEMPLATES, render

D = date(2026, 11, 2)
MED = {"id": 1, "name": "Demozine", "dose_mg": 10}


def logs(statuses: dict[int, str]) -> dict[date, dict]:
    return {D + timedelta(days=k): {"status": s, "hours": 8.0 if s == "taken" else None} for k, s in statuses.items()}


def test_a_blank_day_never_counts_as_missed():
    rows = feature_rows(MED, logs({0: "taken", 1: "missed", 3: "missed", 4: "missed", 5: "unknown", 6: "taken"}), [],
                        D, D + timedelta(days=6))
    by = {r["date"]: r for r in rows}
    assert by[D + timedelta(days=2)]["med_status"] == "unknown"   # no answer
    assert by[D + timedelta(days=4)]["consecutive_missed"] == 2   # days 3-4; the blank day 2 broke the run
    assert by[D + timedelta(days=5)]["consecutive_missed"] == 0   # "don't remember" isn't a miss
    last = by[D + timedelta(days=6)]
    assert last["known_7d"] == 5 and last["adherence_7d"] == pytest.approx(2 / 5, abs=1e-3)  # unknowns left out
    assert render("weekly_count", name="Demozine", k=2, u=2) == "Demozine: taken 2 of the last 7 days (2 not recorded)"


def test_dose_features():
    changes = [{"date": D + timedelta(days=3), "old_dose_mg": 10, "new_dose_mg": 15}]
    rows = feature_rows({**MED, "dose_mg": 15}, logs({k: "taken" for k in range(7)}), changes, D, D + timedelta(days=6))
    assert [r["dose_mg"] for r in rows] == [10, 10, 10, 15, 15, 15, 15]
    assert [r["days_since_dose_change"] for r in rows] == [None, None, None, 0, 1, 2, 3]
    assert rows[-1]["dose_time_local"] == 8.0 and rows[-1]["dose_time_sd_14d"] == 0.0


def test_messages_only_from_templates():
    with pytest.raises(ValueError):
        render("missed_in_a_row", n=2, extra="take two tomorrow")
    with pytest.raises(ValueError):
        render("readiness", symptom="headache")
    with pytest.raises(KeyError):
        render("free_text")
    text = render("low_mood", n=3)
    assert "988" in text and "prescriber" in text
    for t in TEMPLATES.values():  # no dosing advice in any template
        low = t.lower()
        assert not any(w in low for w in ("take it", "double", "skip", "stop taking", "increase", "reduce", " mg"))


def test_medication_is_never_an_experiment():
    from healthtracker.stats.experiments import PRESETS, protocol_for

    assert all(not is_sensitive(k) for k in PRESETS)
    for expo in ("med_status_missed >= 1", "consecutive_missed >= 2", "dose_time_local >= 21", "adherence_7d >= 1"):
        assert is_sensitive(expo)
        with pytest.raises(ValueError, match="never an experiment"):
            protocol_for(expo, "sleep_total_min", 1)


# ------------------------------------------------------------------ on the database


def _streak_day(conn) -> date:
    from healthtracker.db import fetch_one

    r = fetch_one(conn, """SELECT date FROM analytics.medication_daily WHERE consecutive_missed >= 2
                           ORDER BY date LIMIT 1""")
    return r["date"] + timedelta(days=1)


@pytest.mark.db
def test_demo_adherence_features_and_messages(seeded, conn):
    from healthtracker.db import fetch_all
    from healthtracker.medication.messages import fields

    f = fetch_all(conn, "SELECT * FROM analytics.medication_daily WHERE date BETWEEN %s AND %s ORDER BY date",
                  (seeded["start"], seeded["end"]))
    assert len(f) == 90 and 0.85 < f[-1]["adherence_28d"] <= 1
    assert all(r["med_status"] != "missed" for r in f if r["known_7d"] == 0)
    day = _streak_day(conn)
    msgs = fetch_all(conn, "SELECT * FROM analytics.medication_message ORDER BY date, template")
    assert any(m["template"] == "missed_in_a_row" and m["date"] == day and m["visible"] for m in msgs)
    assert any(m["template"] == "weekly_count" and m["date"].weekday() == 0 for m in msgs)
    for m in msgs:  # every stored message is its template with exactly its fields
        assert set(m["params"]) == fields(m["template"]) and m["text"] == render(m["template"], **m["params"])
    assert all(not m["visible"] for m in msgs if m["template"] == "readiness")  # shadow for two weeks


@pytest.mark.db
def test_the_llm_never_sees_medication(tx, monkeypatch):
    from healthtracker.db import fetch_one
    from healthtracker.llm import phrase as phrase_mod

    conn = tx
    day = _streak_day(conn)
    sent: list[str] = []

    def fake_chat(system, user, schema, temperature=0.2):
        sent.append(user)  # the data sent; the fixed instructions name what never to mention
        payload = json.loads(user)
        return {"summary": "Status as given.",
                "items": [{"action_id": a["id"], "message": a["text"]} for a in payload["actions"]]}

    monkeypatch.setattr(phrase_mod, "chat_json", fake_chat)
    phrase_mod.phrase(conn, day, use_llm=True)
    assert sent, "the LLM was called"
    rec = fetch_one(conn, "SELECT * FROM analytics.recommendation WHERE date = %s AND kind = 'daily'", (day,))
    for text in [*sent, json.dumps(rec["input_snapshot"]), rec["rendered_text"] or ""]:
        low = text.lower()
        assert "demozine" not in low and "medication" not in low and "adherence" not in low
        assert "consecutive_missed" not in low and "dose_" not in low and "prescriber" not in low


@pytest.mark.db
def test_a_dose_change_resets_baseline_maturity(tx):
    from healthtracker.db import fetch_df, fetch_one, insert
    from healthtracker.features.baselines import compute_metric_status, load_params, reset_dates

    conn = tx
    f = fetch_df(conn, "SELECT * FROM analytics.daily_features ORDER BY date")
    f["date"] = f["date"].astype("datetime64[ns]")
    f = f.set_index("date")
    change = f.index[-20].date()
    before = compute_metric_status(f, load_params(conn), reset_dates(conn))
    med = fetch_one(conn, "SELECT id FROM core.medication LIMIT 1")["id"]
    insert(conn, "core.medication_change", {"medication_id": med, "date": change, "old_dose_mg": 10,
                                            "new_dose_mg": 15}, returning=None)
    assert change in reset_dates(conn)
    after = compute_metric_status(f, load_params(conn), reset_dates(conn))

    def maturity(df):
        r = df[(df["metric"] == "ln_hrv") & (df["date"] == f.index[-10])]
        return float(r["maturity"].iloc[0])

    assert maturity(after) < maturity(before)
    row = after[(after["metric"] == "ln_hrv") & (after["date"] == f.index[-10])].iloc[0]
    assert bool(row["widened"])


@pytest.mark.db
def test_today_card_flow(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import connect, execute

    client = TestClient(app)
    mid = client.post("/api/medications", json={"name": "Testamol", "dose_mg": 5,
                                                "start_date": "2026-01-01"}).json()["id"]
    try:
        day = date(2026, 12, 7)
        card = {m["id"]: m for m in client.get(f"/api/medication/today?day={day}").json()["medications"]}[mid]
        assert card["today"] is None and card["ask_yesterday"] and "0 of the last 7 days (7 not recorded)" in card["week"]
        card = {m["id"]: m for m in client.put(f"/api/medications/{mid}/log/{day}", json={"status": "taken"}).json()
                ["medications"]}[mid]
        assert card["today"]["status"] == "taken" and card["today"]["taken_at"]
        back = client.delete(f"/api/medications/{mid}/log/{day}").json()  # "Not yet"
        assert {m["id"]: m for m in back["medications"]}[mid]["today"] is None
        y = day - timedelta(days=1)
        client.put(f"/api/medications/{mid}/log/{y}", json={"status": "unknown"})
        card = {m["id"]: m for m in client.get(f"/api/medication/today?day={day}").json()["medications"]}[mid]
        assert not card["ask_yesterday"] and card["yesterday"]["status"] == "unknown"
        assert "0 of the last 7 days (7 not recorded)" in card["week"]  # don't remember is not a miss
        assert client.put(f"/api/medications/{mid}/log/{day}", json={"status": "skipped"}).status_code == 422
        assert client.post(f"/api/medications/{mid}/dose-change", json={"date": str(day), "new_dose_mg": 10}).json()["ok"]
        meds = {m["id"]: m for m in client.get("/api/medications").json()}
        assert meds[mid]["dose_mg"] == 10 and meds[mid]["changes"][0]["old_dose_mg"] == 5
    finally:
        with connect(db_url) as c:
            execute(c, "DELETE FROM core.medication WHERE id = %s", (mid,))


def test_h3_findings_pick_the_right_template():
    from healthtracker.medication.analysis import findings

    p = {"min_missed_days": 3, "travel_share_confounded": 0.7, "finding_min_p": 0.6, "min_missed_for_finding": 8}
    weak = [{"exposure": "med_missed", "outcome": "hrv", "lag_days": 1, "estimate": -2.0, "p_meaningful": 0.3,
             "n_exposed": 12}]
    assert findings(weak, 0.2, 6, "Demozine", p) == [("missed_effect_early", {"outcome": "HRV, resting HR, sleep, or mood"})]
    assert findings(weak, 0.85, 6, "Demozine", p)[0][0] == "travel_confounded"
    strong = [{**weak[0], "estimate": -6.4, "p_meaningful": 0.86},
              {"exposure": "med_dose_time", "outcome": "sleep_total_min", "lag_days": 1, "estimate": -14.0,
               "p_meaningful": 0.7}]
    out = dict(findings(strong, 0.1, 6, "Demozine", p))
    assert render("missed_effect", **out["missed_effect"]) == "After a missed dose, HRV was 6 ms lower (likely)."
    # Two missed days can't carry a finding, however confident the estimate looks.
    few = [{**strong[0], "n_exposed": 2}]
    assert findings(few, 0.1, 2, "Demozine", p) == [("missed_effect_early", {"outcome": "HRV, resting HR, sleep, or mood"})]
    text = render("dose_time", **out["dose_time"])
    assert "14 min lower per hour later (possibly)" in text and text.endswith("whether timing matters.")


@pytest.mark.db
def test_h3_on_the_demo_is_private_and_finds_nothing_planted(seeded, tx):
    from healthtracker.db import fetch_all
    from healthtracker.medication.analysis import run_med_analysis

    conn = tx
    assert run_med_analysis(conn, seeded["end"]) > 0
    cols = {r["column_name"] for r in fetch_all(conn, """SELECT column_name FROM information_schema.columns
                                                      WHERE table_schema = 'analytics' AND table_name = 'daily_features'""")}
    assert not {c for c in cols if c.startswith(("med_", "dose_", "adherence"))}  # never in daily_features
    eff = fetch_all(conn, "SELECT * FROM analytics.medication_effect WHERE computed_on = %s", (seeded["end"],))
    missed = [e for e in eff if e["exposure"] == "med_missed" and e["p_meaningful"] is not None]
    assert missed and all(e["p_meaningful"] < 0.8 for e in missed)  # no physiology was planted
    msgs = fetch_all(conn, """SELECT * FROM analytics.medication_message WHERE date = %s
                              AND template IN ('missed_effect', 'missed_effect_early', 'travel_confounded', 'dose_time')""",
                     (seeded["end"],))
    assert msgs and not any(m["visible"] for m in msgs)  # shadow for the first month
