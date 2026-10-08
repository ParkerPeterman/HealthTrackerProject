"""Turn the day's structured decisions into a short coaching message.

Two small calls (training/recovery, nutrition), each with a compact JSON input
and JSON-schema output. Every output is checked (guard.py); on any failure or
if Ollama is down, a deterministic template is used and the reason is stored.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import psycopg

from healthtracker.config import get_settings
from healthtracker.db import execute, fetch_all, fetch_one
from healthtracker.jsonutil import to_jsonable
from healthtracker.llm.client import LLMUnavailable, chat_json
from healthtracker.llm.guard import check_output
from healthtracker.profile import load_profile
from healthtracker.units import Display

log = logging.getLogger("healthtracker.llm")

NUTRITION_DOMAINS = {"nutrition", "body"}
NUTRITION_METRICS = {"kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "caffeine_mg",
                     "last_caffeine_hour", "alcohol_drinks", "last_meal_hour"}

SYSTEM_PROMPT = """You write the short morning message for a personal training system.
Code has already decided everything; your only job is wording.

You get JSON with today's status and a list of actions (each has an id and text).
Return JSON: {"summary": str, "items": [{"action_id": int, "message": str}]}.

Rules:
- summary: 1-3 plain sentences describing today's status. Describe only; give no advice.
- items: exactly one item per action id in the input, no more, no fewer.
- Each message restates its action in friendly second person and explains why, in 1-2 sentences,
  using only the reason given in the action text or the status numbers. Add no reasons of your own
  (nothing like "to prevent injury" or "to support recovery") and no comparisons the input doesn't make.
- Keep every target number from the action text exactly as written.
- Use only numbers that appear in the input. Never calculate, convert, or estimate new numbers.
- Never add foods, exercises, supplements, durations, or targets that are not in the actions.
- No medical claims or diagnoses. No emojis. No greetings like "Good morning".
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"action_id": {"type": "integer"}, "message": {"type": "string"}},
                "required": ["action_id", "message"],
            },
        },
    },
    "required": ["summary", "items"],
}


def group_of(domain: str, metric: str) -> str:
    if domain in NUTRITION_DOMAINS or metric in NUTRITION_METRICS or metric.startswith("nutrient:"):
        return "nutrition"
    return "training"


def build_payloads(rec: dict, actions: list[dict], display: Display) -> dict[str, dict]:
    snap, dec = rec["input_snapshot"], rec["decisions"]
    status = snap.get("status", {})
    domains = {f["rule_id"]: f["domain"] for f in dec.get("fired", [])}
    visible_flags = [f for f in dec.get("fired", []) if f.get("visible")]
    groups: dict[str, dict] = {}
    for g in ("training", "nutrition"):
        flags = [{"rule": f["description"], "severity": {1: "info", 2: "caution", 3: "important"}.get(f["severity"])}
                 for f in visible_flags if group_of(f["domain"], "") == g]
        acts = [{"id": a["id"], "text": a["action_text"]} for a in actions
                if group_of(domains.get(a["rule_id"], ""), a["metric"]) == g]
        if g == "training":
            ctx = {"recovery": status.get("recovery"), "training": status.get("training"),
                   "journal_yesterday": status.get("journal_yesterday"),
                   "upcoming_sessions": snap.get("upcoming"), "injuries": snap.get("injuries")}
        else:
            ctx = {"nutrition_yesterday": status.get("nutrition_yesterday") or "food log not complete",
                   "body": status.get("body")}
        groups[g] = to_jsonable({
            "date": rec["date"], "weekday": f"{rec['date']:%A}" if isinstance(rec["date"], date) else None,
            "units": {"weight": display.weight_unit, "distance": display.distance_unit},
            "status": {k: v for k, v in ctx.items() if v},
            "flags": flags,
            "actions": acts,
            "last_7_days": snap.get("last_7_days"),
            "insights": snap.get("insights") or None,
        })
    return groups


# ------------------------------------------------------------------ template
def template_summary(group: str, payload: dict) -> str:
    st = payload.get("status", {})
    if group == "training":
        r = st.get("recovery") or {}
        parts = []
        if r.get("hrv_ms") is not None:
            z = r.get("hrv_vs_baseline_sd")
            parts.append(f"HRV {r['hrv_ms']} ms" + (f" ({z:+.1f} SD vs baseline)" if z is not None else ""))
        if r.get("resting_hr_bpm") is not None:
            parts.append(f"resting HR {r['resting_hr_bpm']}")
        if r.get("sleep_last_night"):
            parts.append(f"sleep {r['sleep_last_night']}" + (f" (score {r['sleep_score']})" if r.get("sleep_score") else ""))
        text = ("Recovery: " + ", ".join(parts) + ".") if parts else "No overnight data yet today."
        if not payload["actions"]:
            text += " No training flags today."
        return text
    n = st.get("nutrition_yesterday")
    if isinstance(n, dict):
        bits = [f"{n['kcal']} kcal" if n.get("kcal") is not None else None,
                f"{n['protein_g']} g protein" if n.get("protein_g") is not None else None]
        text = "Yesterday: " + ", ".join(b for b in bits if b) + "."
    else:
        text = "Yesterday's food log isn't marked complete."
    if not payload["actions"]:
        text += " No nutrition flags today."
    return text


def render(sections: dict[str, dict], payloads: dict[str, dict]) -> str:
    titles = {"training": "Training & recovery", "nutrition": "Nutrition"}
    out = []
    for g in ("training", "nutrition"):
        sec = sections[g]
        texts = {a["id"]: a["text"] for a in payloads[g]["actions"]}
        out.append(f"**{titles[g]}**\n{sec['summary'].strip()}")
        for item in sec["items"]:
            msg = item.get("message") or texts.get(item["action_id"], "")
            out.append(f"- {msg.strip()}")
    return "\n".join(out).strip()


def template_sections(payloads: dict[str, dict]) -> dict[str, dict]:
    return {g: {"summary": template_summary(g, p),
                "items": [{"action_id": a["id"], "message": a["text"]} for a in p["actions"]]}
            for g, p in payloads.items()}


# ------------------------------------------------------------------ main
def phrase(conn: psycopg.Connection, d: date, use_llm: bool | None = None) -> str | None:
    s = get_settings()
    use_llm = s.llm_enabled if use_llm is None else use_llm
    rec = fetch_one(conn, "SELECT * FROM analytics.recommendation WHERE date = %s AND kind = 'daily'", (d,))
    if not rec:
        return None
    actions = fetch_all(conn, """SELECT * FROM analytics.recommendation_action
                                 WHERE recommendation_id = %s AND visible ORDER BY id""", (rec["id"],))
    display = Display(load_profile(conn).display_units)
    payloads = build_payloads(rec, actions, display)

    check: dict = {"mode": "template", "groups": {}}
    sections = template_sections(payloads)
    output_check, model = None, None
    if use_llm:
        model = s.llm_model
        llm_sections, ok = {}, True
        for g, payload in payloads.items():
            group_actions = [a for a in actions if a["id"] in {x["id"] for x in payload["actions"]}]
            result = _phrase_group(payload, group_actions)
            check["groups"][g] = result["check"]
            if result["output"] is None:
                ok = False
                break
            llm_sections[g] = result["output"]
        if ok:
            sections, output_check, check["mode"] = llm_sections, "passed", "llm"
        else:
            output_check, check["mode"] = "failed_fallback_used", "template_fallback"

    text = render(sections, payloads)
    execute(conn,
            """UPDATE analytics.recommendation SET rendered_text = %s, llm_model = %s,
               prompt_version = %s, output_check = %s, llm_check = %s WHERE id = %s""",
            (text, model, s.prompt_version, output_check, json.dumps(to_jsonable(check)), rec["id"]))
    return text


def _phrase_group(payload: dict, actions: list[dict], attempts: int = 2) -> dict:
    tries = []
    for attempt in range(attempts):
        try:
            out = chat_json(SYSTEM_PROMPT, json.dumps(payload, ensure_ascii=False), OUTPUT_SCHEMA,
                            temperature=0.2 if attempt == 0 else 0.0)
        except LLMUnavailable as exc:
            return {"output": None, "check": {"passed": False, "problems": [str(exc)], "attempts": tries}}
        except ValueError as exc:
            tries.append({"problems": [str(exc)]})
            continue
        result = check_output(out, payload, actions)
        if result.passed:
            return {"output": out, "check": {"passed": True, "attempts": len(tries) + 1}}
        tries.append({"problems": result.problems, "output": out})
        log.info("LLM output failed check: %s", result.problems)
    return {"output": None, "check": {"passed": False, "attempts": tries}}
