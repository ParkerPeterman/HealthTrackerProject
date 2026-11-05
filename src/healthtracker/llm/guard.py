"""Output check for LLM messages.

The model must (1) cover exactly the actions the rules made, once each,
(2) use no number that isn't in its input, (3) keep each action's target
numbers, (4) keep the summary descriptive, with no advice of its own, and
(5) say nothing about medication, painkillers, or drinking extra water. Medication
lines come only from approved templates (medication/messages.py); painkillers can
interact with some prescriptions, so injuries point to a physio or doctor instead;
and pushing fluids past the day's goal is the main cause of low sodium in exercise.
Any failure means the plain template is used instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

NUMBER = re.compile(r"\d+(?:\.\d+)?")
ADVICE = re.compile(
    r"\b(should|try|avoid|eat|drink|take|skip|make sure|go to bed|consider|aim|limit|add|"
    r"supplement|stretch|hydrate|rest up|cut back|increase|reduce)\b", re.IGNORECASE)
BANNED = re.compile(
    r"\b(medication|medicine|prescri\w*|doses?|dosing|pills?|ibuprofen|naproxen|aspirin|nsaids?|painkillers?|"
    r"pain relievers?|advil|motrin|aleve|acetaminophen|tylenol|paracetamol|"
    r"drink (?:more|plenty|extra|lots)|plenty of (?:water|fluids)|extra (?:water|fluids)|hydrate more)\b",
    re.IGNORECASE)
MAX_SUMMARY = 600
MAX_MESSAGE = 450


@dataclass
class CheckResult:
    passed: bool
    problems: list[str] = field(default_factory=list)


def numbers_in(text: str) -> set[str]:
    return {_canon(m) for m in NUMBER.findall(text or "")}


def _canon(token: str) -> str:
    f = float(token)
    return str(int(f)) if f == int(f) else f"{f:g}"


def allowed_numbers(payload: dict) -> set[str]:
    """Every number in the input, plus its rounding to 0 and 1 decimals."""
    text = json.dumps(payload, ensure_ascii=False)
    out: set[str] = set()
    for tok in NUMBER.findall(text):
        f = float(tok)
        out.add(_canon(tok))
        out.add(str(int(round(f))))
        out.add(f"{round(f, 1):g}")
    return out


def target_tokens(action: dict) -> set[str]:
    """Target numbers that appear verbatim in the action text must survive phrasing."""
    toks = set()
    text_nums = numbers_in(action["action_text"])
    for key in ("target_value", "target_value_high"):
        v = action.get(key)
        if v is None:
            continue
        tok = _canon(str(v))
        if tok in text_nums:
            toks.add(tok)
    return toks


def check_output(output: dict, payload: dict, actions: list[dict]) -> CheckResult:
    problems: list[str] = []
    if not isinstance(output, dict) or "summary" not in output or "items" not in output:
        return CheckResult(False, ["output does not match schema"])
    summary = str(output.get("summary") or "")
    items = output.get("items") or []
    if not isinstance(items, list):
        return CheckResult(False, ["items is not a list"])

    expected = {a["id"] for a in actions}
    got = [i.get("action_id") for i in items if isinstance(i, dict)]
    if len(got) != len(set(got)):
        problems.append("an action is covered more than once")
    if set(got) != expected:
        missing, extra = expected - set(got), set(got) - expected
        if missing:
            problems.append(f"missing actions {sorted(missing)}")
        if extra:
            problems.append(f"actions not made by the rules {sorted(extra)}")

    allowed = allowed_numbers(payload)
    texts = [summary] + [str(i.get("message") or "") for i in items if isinstance(i, dict)]
    stray = sorted({n for t in texts for n in numbers_in(t)} - allowed)
    if stray:
        problems.append(f"numbers not in the input: {stray}")

    by_id = {a["id"]: a for a in actions}
    for i in items:
        if not isinstance(i, dict) or i.get("action_id") not in by_id:
            continue
        msg = str(i.get("message") or "")
        need = target_tokens(by_id[i["action_id"]])
        if need - numbers_in(msg):
            problems.append(f"action {i['action_id']} lost its target {sorted(need - numbers_in(msg))}")
        if len(msg) > MAX_MESSAGE:
            problems.append(f"action {i['action_id']} message too long")
        if not msg.strip():
            problems.append(f"action {i['action_id']} message empty")

    for t in texts:
        if m := BANNED.search(t):
            problems.append(f"mentions '{m.group(0)}', which only code-made templates may cover")
    if ADVICE.search(summary):
        problems.append(f"summary gives advice ('{ADVICE.search(summary).group(0)}')")
    if len(summary) > MAX_SUMMARY:
        problems.append("summary too long")
    return CheckResult(not problems, problems)
