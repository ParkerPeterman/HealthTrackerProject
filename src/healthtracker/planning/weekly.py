"""Weekly plan generator (F3): next week's sessions from the season phase's template.

Code decides every session, day, and size; the LLM is not involved.

1. Template. The season phase (F1) picks a template (planning/templates.py) of library
   sessions (F2) and the days they prefer. Main days are Monday to Thursday by default;
   Friday to Sunday are optional bonus slots for short, equipment-light sessions, so the
   plan works with the main days alone. Tokens resolve to this week's VO2 and Copenhagen
   levels and the sprint rotation.
2. Placement, greedy, key sessions first:
   * games and my own planned sessions stay fixed; nothing new goes on a game day
   * the days before and after a game are easy: no hard sessions, no lower-body strength,
     so quality sessions land at least 48 hours before a game
   * hard days are spaced. Two hard endurance days, or two hard neuromuscular days (sprints,
     jumps, heavy legs), are never back to back, and sprints never follow a hard endurance
     day; a hard endurance day may follow a sprint day. (strict_spacing = 1 keeps every hard
     day apart, which leaves room for at most two in four days.) A game counts as both.
   * at most one run a day, lower-body strength never on consecutive days, and the day's
     minutes within what I have
   * the library's checks for the day: phase, recent hamstring or groin tags, sore
     shoulders, a below-band HRV (first two days only; the daily adjustment handles the
     rest), and active injury restrictions
3. Size. Durations start mid-range (low end in a deload). The ramp caps (D1/A6, from
   planning/ramp.py: the same caps the rules flag) bound running load, sprint metres, and
   jump contacts. Optional sessions go first, then non-key sessions shrink or drop; a sprint
   session too small to matter becomes sprint technique. Total internal load aims at my
   chronic load times a factor and never exceeds its own cap. The planned easy share of
   running is reported against the D4 target.
   Progression (F5, planning/progression.py): the factor is above 1 only after a week whose
   key sessions were done and whose recovery held; it holds at 1 otherwise, drops after
   recent recovery flags, and drops further in a deload. Every 4th week of base, VO2 build,
   and pre-season (`deload_every`) is a deload week: key sessions at the low end of their
   range, everything else shorter by the deload factor, fewer sprint and jump reps, nothing
   lengthened back toward the target, optional sessions dropped while over it, and the VO2
   level held.
4. Health (F6, planning/health.py): the WHO weekly aerobic minutes and strength days, counted
   from the planned sessions; easy runs lengthen toward 150 minutes inside the caps (not in a
   deload or after recovery flags); a sleep-regularity note when the SRI is low; the protein
   target in grams a day.
   Taper (F1, in season and pre-season): a week with a tournament (two or more games in a day,
   or games on back-to-back days) keeps its key sessions but no hard session in the
   `taper_days` (3) before it, shortens the rest by `taper_factor` (0.7), and aims the load at
   the chronic load times that factor without lengthening anything back.
5. Return to ultimate (F7): in pre-season, weeks 1-2 are accelerations only, weeks 3-4 add
   max-velocity sprinting and cutting, week 5 on everything (the library's `return_stage`,
   checked per day); sprint metres are scaled 0.5 / 0.75 / 1.0 before the ramp caps.
6. Output. Each session carries one structured reason. Plans are versioned: the same inputs
   give the same plan and no new version. Each week's plan is graded afterwards on
   adherence (planned sessions matched to workouts) and response.

Weather (A7): on a day the forecast calls icy or very cold, outdoor intervals and runs are
marked for a treadmill or bike, and outdoor sprint work is dropped (the day stays a strength
day). The medication readiness flag (H4) plugs in here once it leaves shadow.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import psycopg

from healthtracker.db import execute, fetch_all, fetch_one, insert, upsert
from healthtracker.features.load import current_factor
from healthtracker.ingest.weather import ATTRIBUTION, bad_for_outdoors, daily_forecast
from healthtracker.jsonutil import to_jsonable
from healthtracker.planning.health import gather_protein, gather_sleep, plan_health, render_health
from healthtracker.planning.library import (
    PHASE_WORDS,
    DayState,
    active_restrictions,
    check,
    copenhagen_level,
    day_state,
    exercise_meta,
    exercises_for,
    load_library,
    mid_minutes,
)
from healthtracker.planning.prehab import shoulder_state
from healthtracker.planning.progression import (
    RETURN_ADDS,
    RETURN_SCALE,
    RETURN_WORDS,
    phase_week,
    progression_check,
    return_stage,
    return_weeks,
    scheduled_deload,
)
from healthtracker.planning.ramp import ramp_cap
from healthtracker.planning.season import phase_for, phases
from healthtracker.planning.templates import GOAL_ROLES, ROTATIONS, TEMPLATES, Slot
from healthtracker.profile import load_profile
from healthtracker.rules.context import load_injuries, load_params

PLANNER_VERSION = "f7.2"
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_WORDS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
LOWER = {"quads", "hamstrings", "glutes", "calves", "adductors"}
DEFAULTS = {"progression_factor": 1.05, "caution_factor": 0.85, "deload_factor": 0.65, "total_allowance": 100,
            "min_sprint_m": 40, "min_plyo_contacts": 10, "strict_spacing": 0, "deload_every": 4,
            "progress_key_pct": 100, "taper_factor": 0.7, "taper_days": 3}
CAUTION_RULES = ("RECOV_ILLNESS_FLAG", "RECOV_HIGH_LOAD_LOW_HRV", "INJ_RETURN_TO_PLAY")
VO2_CODES = {1: "vo2_3x3", 2: "vo2_4x3", 3: "vo2_3x4", 4: "vo2_4x4"}


# ------------------------------------------------------------------ inputs
@dataclass
class PlanInputs:
    """Everything the generator looks at. Built from the database by gather(), or by hand in tests."""

    week_start: date
    phase: str
    states: dict[date, DayState]                 # library view of each day
    fixed: list[dict] = field(default_factory=list)  # planned_session rows that stay (games, my own)
    prev_game: bool = False                      # a game the Sunday before
    next_game: bool = False                      # a game the Monday after
    main_days: tuple[int, ...] = (0, 1, 2, 3)
    bonus_days: tuple[int, ...] = (4, 5, 6)
    day_minutes: int = 120
    bonus_minutes: int = 45
    last_week: dict[str, float | None] = field(default_factory=dict)  # run, sprint, plyo, total
    chronic_week: float | None = None            # chronic internal load per week (uncoupled EWMA x 7)
    caution: list[str] = field(default_factory=list)
    vo2_level: tuple[int, str] = (1, "starting at 3 x 3")
    copenhagen: tuple[int, str] = (1, "starting at level 1")
    goals: list[str] = field(default_factory=list)
    params: dict[str, dict[str, float]] = field(default_factory=dict)
    factor: float = 0.4                          # TRIMP units per sRPE unit (A6 calibration)
    weather: dict[date, dict] = field(default_factory=dict)  # home daily forecast (A7)
    phase_week: int | None = None                # week of the phase (1 = its first week)
    progress: tuple[bool, str] = (True, "recovery looks normal: small progression")  # F5 check of last week
    sleep: dict = field(default_factory=dict)    # sri (28 days) and weekend gap, once E1 is live (F6)
    protein: dict | None = None                  # the protein target in g a day (F6)

    def p(self, name: str) -> float:
        return float(self.params.get("ANALYSIS_PLAN", {}).get(name, DEFAULTS[name]))

    @property
    def deload_week(self) -> bool:
        """A scheduled deload week inside a training phase (F5)."""
        return scheduled_deload(self.phase, self.phase_week, self.p("deload_every"))

    @property
    def deloading(self) -> bool:
        return self.phase == "deload" or self.deload_week

    @property
    def tournaments(self) -> list[date]:
        """Tournament days this week: two or more games in a day, or games on back-to-back days."""
        if self.phase in ("deload", "transition"):
            return []
        per: dict[date, int] = {}
        for s in self.fixed:
            if s["session_type"] == "game":
                per[s["date"]] = per.get(s["date"], 0) + 1
        sunday = self.week_start + timedelta(days=6)
        days = {d for d, n in per.items() if n >= 2 or (d + timedelta(days=1)) in per
                or (d - timedelta(days=1)) in per or (d == sunday and self.next_game)}
        return sorted(days)

    @property
    def taper(self) -> bool:
        return bool(self.tournaments)

    @property
    def return_stage(self) -> int | None:
        """F7: the return-to-ultimate stage in pre-season (1-3), None otherwise."""
        return return_stage(self.phase, self.phase_week, self.params)


# ------------------------------------------------------------------ placement
@dataclass
class Placed:
    slot: Slot
    entry: dict
    day: int
    minutes: int
    addons: list[dict]
    sprint_m: int
    plyo: int
    moved: str | None = None
    notes: list[str] = field(default_factory=list)
    swaps: list[str] = field(default_factory=list)
    focus: list[str] | None = None

    @property
    def code(self) -> str:
        return self.entry["code"]

    @property
    def run(self) -> bool:
        return self.entry["sport"] == "run"

    def load(self, factor: float) -> float:
        main = self.minutes * self.entry["rpe"] * factor
        return round(main + sum(_mid(a) * a["rpe"] * factor for a in self.addons), 1)

    def run_load(self, factor: float) -> float:
        main = self.minutes * self.entry["rpe"] * factor if self.run else 0.0
        return round(main + sum(_mid(a) * a["rpe"] * factor for a in self.addons if a["sport"] == "run"), 1)

    @property
    def total_minutes(self) -> int:
        return self.minutes + sum(_mid(a) for a in self.addons)


_mid = mid_minutes


def kind(entry: dict) -> str | None:
    """Hard endurance (E) or hard neuromuscular (N); None for everything else."""
    if not entry["hard"]:
        return None
    return "E" if entry["sport"] == "run" else "N"


def is_lower_strength(entry: dict, meta: dict) -> bool:
    return entry["category"] == "strength" and any(
        meta.get(x["exercise"], {}).get("primary", set()) & LOWER for x in entry.get("exercises") or [])


def fixed_kinds(s: dict) -> set[str]:
    if s["session_type"] == "game":
        return {"E", "N"}
    if s["session_type"] == "hard":
        return {"E"} if s["sport"] == "run" else {"N"}
    return set()


def spacing_ok(k: str | None, prev: set[str], same: set[str], nxt: set[str], strict: bool) -> bool:
    if k is None:
        return True
    if same - {k}:
        return False  # one kind of hard work a day
    if strict:
        return not prev and not nxt
    # The only hard neighbours allowed: a sprint/strength day, then a hard endurance day.
    return all(p == "N" and k == "E" for p in prev) and all(k == "N" and n == "E" for n in nxt)


class Week:
    def __init__(self, inp: PlanInputs, lib: dict[str, dict], meta: dict[str, dict]):
        self.inp, self.lib, self.meta = inp, lib, meta
        self.dates = [inp.week_start + timedelta(days=i) for i in range(7)]
        self.placed: list[Placed] = []
        self.dropped: list[dict] = []
        self.games = {s["date"] for s in inp.fixed if s["session_type"] == "game"}
        self.strict = bool(inp.p("strict_spacing"))

    # -- day facts
    def game(self, i: int) -> bool:
        if i < 0:
            return self.inp.prev_game
        if i > 6:
            return self.inp.next_game
        return self.dates[i] in self.games

    def near_game(self, i: int) -> str | None:
        if self.game(i + 1):
            return "the day before a game"
        if self.game(i - 1):
            return "the day after a game"
        return None

    def kinds(self, i: int) -> set[str]:
        if i < 0:
            return {"E", "N"} if self.inp.prev_game else set()
        if i > 6:
            return {"E", "N"} if self.inp.next_game else set()
        out = set().union(*[fixed_kinds(s) for s in self.inp.fixed if s["date"] == self.dates[i]])
        return out | {k for p in self.placed if p.day == i and (k := kind(p.entry))}

    def minutes(self, i: int) -> int:
        fixed = sum(s.get("planned_duration_min") or 0 for s in self.inp.fixed
                    if s["date"] == self.dates[i] and s["session_type"] != "game")
        return fixed + sum(p.total_minutes for p in self.placed if p.day == i)

    def budget(self, i: int, slot: Slot) -> int:
        if i in self.inp.main_days:
            return self.inp.day_minutes
        return slot.budget or self.inp.bonus_minutes

    def has_run(self, i: int) -> bool:
        return any(p.day == i and p.run for p in self.placed) or any(
            s["date"] == self.dates[i] and s["sport"] == "run" for s in self.inp.fixed)

    def lower_strength(self, i: int) -> bool:
        return any(p.day == i and is_lower_strength(p.entry, self.meta) for p in self.placed) or any(
            s["date"] == self.dates[i] and s["session_type"] == "strength" and set(s.get("focus_muscles") or []) & LOWER
            for s in self.inp.fixed)

    def used(self, code: str) -> bool:
        return any(p.code == code for p in self.placed)

    # -- can this session go on this day?
    def why_not(self, slot: Slot, e: dict, i: int, minutes: int) -> list[str]:
        d = self.dates[i]
        out = check(e, self.inp.states[d], self.meta)
        if self.game(i):
            return ["game day", *out]
        bonus = i not in self.inp.main_days
        if bonus and not e["travel_ok"]:
            out.append("not a travel-day session")
        near = self.near_game(i)
        if near and (e["hard"] or e["plan_type"] == "moderate" or is_lower_strength(e, self.meta)):
            out.append(near)
        # F1 taper: nothing hard in the last few days before a tournament.
        days = int(self.inp.p("taper_days"))
        if e["hard"] and any(self.dates[i] < t <= self.dates[i] + timedelta(days=days) for t in self.inp.tournaments):
            out.append(f"taper: no hard sessions in the {days} days before the tournament")
        # D5: a session's recovery cost (a prior until learned) sets how many days it needs before a game.
        need = -(-int(e.get("recovery_hours") or 0) // 24)
        if need > 1 and any(self.game(i + k) for k in range(1, need)):
            out.append(f"needs {need} days before a game (recovery cost)")
        if not spacing_ok(kind(e), self.kinds(i - 1), self.kinds(i), self.kinds(i + 1), self.strict):
            out.append("too close to another hard day")
        if e["sport"] == "run" and self.has_run(i):
            out.append("already a run that day")
        if is_lower_strength(e, self.meta) and (self.lower_strength(i - 1) or self.lower_strength(i + 1)):
            out.append("lower-body strength the day before or after")
        if self.minutes(i) + minutes > self.budget(i, slot):
            out.append(f"over the {self.budget(i, slot)} minutes that day")
        if not slot.repeat and self.used(e["code"]):
            out.append("already in the week")
        return out

    def day_order(self, slot: Slot) -> list[int]:
        pref = self.slot_day(slot)
        pool = list(self.inp.main_days) if pref in self.inp.main_days else list(self.inp.bonus_days)
        if not slot.move:
            return [pref]
        return [pref] + sorted((i for i in pool if i != pref), key=lambda i: (abs(i - pref), i))

    def slot_day(self, slot: Slot) -> int:
        main, bonus = self.inp.main_days, self.inp.bonus_days
        if slot.day < 4:
            return main[min(slot.day, len(main) - 1)] if main else slot.day
        return bonus[min(slot.day - 4, len(bonus) - 1)] if bonus else slot.day

    def resolve(self, code: str) -> str:
        if code == "{vo2}":
            return VO2_CODES[self.inp.vo2_level[0]]
        if code == "{copenhagen}":
            return f"copenhagen_l{self.inp.copenhagen[0]}"
        if code in ROTATIONS:
            return ROTATIONS[code][self.inp.week_start.isocalendar().week % 2]
        return code

    def place(self, slot: Slot) -> None:
        candidates = [self.lib[c] for c in map(self.resolve, slot.candidates) if c in self.lib]
        days = self.day_order(slot)
        tried: dict[str, list[str]] = {}
        pairs = [(e, i) for e in candidates for i in days] if slot.move else [(e, i) for i in days for e in candidates]
        deload = self.inp.deloading
        for e, i in pairs:
            minutes = e["duration_min_lo"] if deload else _mid(e)
            addons = self.addons_for(slot, e, i)
            reasons = self.why_not(slot, e, i, minutes + sum(_mid(a) for a in addons))
            if reasons:
                tried.setdefault(f"{e['code']}@{DAY_NAMES[i]}", reasons)
                continue
            pref = self.slot_day(slot)
            moved = None
            if i != pref:
                why = tried.get(f"{e['code']}@{DAY_NAMES[pref]}") or tried.get(f"{candidates[0]['code']}@{DAY_NAMES[pref]}")
                moved = f"moved from {DAY_WORDS[pref]}" + (f": {why[0]}" if why else "")
            exercises, swaps = exercises_for(e, self.inp.states[self.dates[i]], self.meta)
            focus = sorted(set().union(*[self.meta.get(x["exercise"], {}).get("primary", set()) for x in exercises])) \
                if e["category"] == "strength" else None
            p = Placed(slot, e, i, minutes, addons, e["sprint_m"], e["plyo_contacts"], moved=moved, swaps=swaps,
                       focus=focus or None)
            if e["code"] != self.resolve(slot.candidates[0]):
                first = self.resolve(slot.candidates[0])
                why = tried.get(f"{first}@{DAY_NAMES[i]}") or next((v for k, v in tried.items() if k.startswith(first)), [])
                p.notes.append(f"instead of {self.lib[first]['name'] if first in self.lib else first}"
                               + (f": {why[0]}" if why else ""))
            self.placed.append(p)
            return
        self.dropped.append({"slot": slot.role, "optional": slot.optional,
                             "reasons": {k: v for k, v in list(tried.items())[:6]}})

    def addons_for(self, slot: Slot, main: dict, i: int) -> list[dict]:
        """Add-ons that fit the day: strides only after a run, nothing for the legs next to a game."""
        out = []
        state = self.inp.states[self.dates[i]]
        for code in map(self.resolve, slot.addons):
            a = self.lib.get(code)
            if a is None or check(a, state, self.meta):
                continue
            if a["sport"] == "run" and main["sport"] != "run":
                continue
            lower = any(self.meta.get(x["exercise"], {}).get("primary", set()) & LOWER for x in a.get("exercises") or [])
            if lower and self.near_game(i):
                continue
            out.append(a)
        return out


# ------------------------------------------------------------------ sizing
def trim_order(placed: list[Placed]) -> list[Placed]:
    """What goes first when a cap binds: optional, then non-key, later in the template first."""
    return sorted(placed, key=lambda p: (not p.slot.optional, p.slot.key, -p.day))


def apply_caps(week: Week, room: dict[str, float]) -> None:
    """Bring the generated sessions within the room the ramp caps leave after fixed sessions."""
    inp, f = week.inp, week.inp.factor
    min_sprint, min_plyo = inp.p("min_sprint_m"), inp.p("min_plyo_contacts")
    technique = week.lib.get("speed_technique")

    def drop(p: Placed, why: str) -> None:
        week.placed.remove(p)
        week.dropped.append({"slot": p.slot.role, "optional": p.slot.optional, "reasons": {p.code: [why]}})

    # Sprint metres and jump contacts: fewer reps, then technique or nothing.
    for attr, label, unit, floor in (("sprint_m", "sprint", "m of sprinting", min_sprint),
                                     ("plyo", "jump", "contacts", min_plyo)):
        cap = room["sprint" if attr == "sprint_m" else "plyo"]
        for p in trim_order([p for p in week.placed if getattr(p, attr) > 0]):
            over = sum(getattr(x, attr) for x in week.placed) - cap
            if over <= 0:
                break
            new = int(max(0, getattr(p, attr) - over))
            if new >= floor:
                setattr(p, attr, new)
                p.notes.append(f"{label} volume held to {new} {unit} by the weekly ramp cap")
            elif attr == "sprint_m" and p.entry["category"] == "speed_jump" and technique:
                p.entry, p.sprint_m, p.minutes = technique, 0, _mid(technique)
                p.notes.append("sprint technique only: the weekly sprint ramp cap leaves too little for maximal sprints")
            elif p.entry["category"] == "strength":
                setattr(p, attr, 0)
                p.notes.append("skip the box jumps this week (jump ramp cap)")
            else:
                drop(p, f"weekly {label} ramp cap")

    # Running load, then total load: shorten (optional and non-key first), then drop.
    for which, word in (("run", "running"), ("total", "load")):
        def load(which: str = which) -> float:
            return sum(x.run_load(f) if which == "run" else x.load(f) for x in week.placed)

        def counts(x: Placed, which: str = which) -> bool:
            return which == "total" or x.run_load(f) > 0

        for p in trim_order([x for x in week.placed if counts(x)]):
            before = p.minutes
            while load() > room[which] and p.minutes - 5 >= p.entry["duration_min_lo"]:
                p.minutes -= 5
            if p.minutes < before:
                p.notes.append(f"shortened to {p.minutes} min by the weekly {word} ramp cap")
        for p in trim_order([x for x in week.placed if counts(x)]):
            if load() <= room[which]:
                break
            drop(p, f"weekly {word} ramp cap")


def return_scale(week: Week, stage: int) -> None:
    """F7: sprint metres at half, three quarters, then all of the session's volume."""
    k = RETURN_SCALE[stage]
    if k >= 1:
        return
    for p in week.placed:
        if p.sprint_m and week.inp.states[week.dates[p.day]].return_stage:
            p.sprint_m = int(5 * round(p.sprint_m * k / 5))
            p.notes.append(f"return to ultimate, week {week.inp.phase_week} of pre-season: "
                           f"{'half' if k == 0.5 else 'three quarters'} of the sprint volume ({p.sprint_m} m)")


def deload_scale(week: Week, k: float | None = None, label: str = "deload week") -> None:
    """A scheduled deload week (F5), or a taper (F1): non-key sessions shorter, fewer sprint and jump reps."""
    k = week.inp.p("deload_factor") if k is None else k
    for p in week.placed:
        if not p.slot.key:
            new = max(10, 5 * round(p.minutes * k / 5))
            if new < p.minutes:
                p.minutes = new
                p.notes.append(f"{label}: {new} min")
        for attr, floor, unit in (("sprint_m", week.inp.p("min_sprint_m"), "m of sprinting"),
                                  ("plyo", week.inp.p("min_plyo_contacts"), "jump contacts")):
            v = getattr(p, attr)
            new = min(v, max(int(floor), int(5 * round(v * k / 5))))
            if v and new < v:
                setattr(p, attr, new)
                p.notes.append(f"{label}: {new} {unit}")


def deload_trim(week: Week, target: float | None) -> None:
    """In a deload week the target binds: optional sessions go while the week is over it."""
    if target is None:
        return
    f = week.inp.factor
    for p in trim_order([x for x in week.placed if x.slot.optional]):
        if sum(x.load(f) for x in week.placed) <= target:
            break
        week.placed.remove(p)
        week.dropped.append({"slot": p.slot.role, "optional": True, "reasons": {p.code: ["deload week"]}})


def fit_target(week: Week, target: float | None, room: dict[str, float], grow: bool = True) -> None:
    """Lengthen easy and steady sessions toward the target, or shorten them, within the caps' room."""
    if target is None:
        return
    f = week.inp.factor
    flexible = [p for p in trim_order(week.placed) if not p.entry["hard"] and p.entry["category"] in ("endurance",)]
    total = lambda: sum(p.load(f) for p in week.placed)  # noqa: E731
    run = lambda: sum(p.run_load(f) for p in week.placed)  # noqa: E731
    if total() > target:
        for p in flexible:
            while total() > target and p.minutes - 5 >= p.entry["duration_min_lo"]:
                p.minutes -= 5
    elif grow:
        for p in reversed(flexible):
            while p.minutes + 5 <= p.entry["duration_min_hi"]:
                p.minutes += 5
                if total() > min(target, room["total"]) or run() > room["run"] or \
                        week.minutes(p.day) > week.budget(p.day, p.slot):
                    p.minutes -= 5
                    break


# ------------------------------------------------------------------ the plan
def build_plan(inp: PlanInputs, lib: dict[str, dict], meta: dict[str, dict]) -> dict:
    stage, weeks = inp.return_stage, return_weeks(inp.params)
    if stage:  # every pre-season day checks the stage
        inp = replace(inp, states={d: replace(st, return_stage=stage, return_weeks=weeks) if st.phase == "preseason"
                                   else st for d, st in inp.states.items()})
    week = Week(inp, lib, meta)
    template = TEMPLATES.get(inp.phase, TEMPLATES["base"])
    goal_keys = {GOAL_ROLES[g] for g in inp.goals if g in GOAL_ROLES}
    slots = [replace(s, key=s.key or s.role in goal_keys) for s in template]
    if not inp.bonus_days:
        slots = [s for s in slots if s.day < 4]
    order = sorted(range(len(slots)), key=lambda k: (slots[k].optional, not slots[k].key, k))
    for k in order:
        week.place(slots[k])

    f = inp.factor
    weather = apply_weather(week)
    caps = plan_caps(inp)
    fixed = {"run": sum(_fixed_load(s, f) for s in inp.fixed if s["sport"] == "run"),
             "total": sum(_fixed_load(s, f) for s in inp.fixed), "sprint": 0.0, "plyo": 0.0}
    room = {k: max(0.0, caps[k] - fixed[k]) for k in caps}
    target, why = load_target(inp)
    target = min(target - fixed["total"], room["total"]) if target is not None else None
    if stage:
        return_scale(week, stage)
    if inp.deload_week:
        deload_scale(week)
    elif inp.taper:
        deload_scale(week, inp.p("taper_factor"), "taper for the tournament")
    apply_caps(week, room)
    fit_target(week, target, room, grow=not (inp.deload_week or inp.taper))
    if inp.deloading:
        deload_trim(week, target)
    skip = ("a deload week" if inp.deloading else "a taper week" if inp.taper
            else "recent recovery flags" if inp.caution else None)
    health = plan_health(week, room, extend=skip is None, skip_why=skip)

    sessions = []
    for p in sorted(week.placed, key=lambda p: (p.day, p.entry["day_order"], p.code)):
        e = p.entry
        level = (f" (level {e['level']})" if e["progression_group"] == "copenhagen" else "")
        reason = {"slot": p.slot.role, "phase": inp.phase, "key": p.slot.key, "why": p.slot.why,
                  "day": p.moved or "as planned", "changes": p.notes, "swaps": p.swaps}
        if e["progression_group"] == "vo2":
            reason["progression"] = f"VO2 level {inp.vo2_level[0]} of 4: {inp.vo2_level[1]}"
        if any(a["progression_group"] == "copenhagen" for a in p.addons):
            reason["copenhagen"] = f"Copenhagen level {inp.copenhagen[0]}: {inp.copenhagen[1]}"
        sessions.append({
            "date": week.dates[p.day], "sport": e["sport"], "session_type": e["plan_type"],
            "planned_duration_min": p.minutes, "planned_rpe": e["rpe"], "focus_muscles": p.focus,
            "status": "planned", "origin": "generated", "notes": e["name"] + level,
            "session_code": e["code"], "addons": [a["code"] for a in p.addons] or None,
            "optional": p.slot.optional, "key": p.slot.key, "reason": reason,
            "planned_internal_load": p.load(f), "planned_sprint_m": p.sprint_m or None,
            "planned_plyo_contacts": p.plyo or None,
        })
    totals = {"run": round(sum(p.run_load(f) for p in week.placed), 1),
              "sprint_m": sum(p.sprint_m for p in week.placed), "plyo_contacts": sum(p.plyo for p in week.placed),
              "total": round(sum(p.load(f) for p in week.placed), 1),
              "minutes": sum(p.total_minutes for p in week.placed)}
    run_min = sum(p.minutes for p in week.placed if p.run) + sum(
        _mid(a) for p in week.placed for a in p.addons if a["sport"] == "run")
    hard_min = sum(p.entry.get("hard_min") or 0 for p in week.placed if p.run)
    easy_target = inp.params.get("TRAIN_INTENSITY_MIX", {}).get("easy_target_pct", 75)
    return {
        "week_start": inp.week_start, "phase": inp.phase, "planner_version": PLANNER_VERSION,
        "target_load": round(target + fixed["total"]) if target is not None else None, "reason": why,
        "chronic_weekly_load": round(inp.chronic_week) if inp.chronic_week is not None else None,
        "caps": {k: round(v, 1) for k, v in caps.items()}, "last_week": inp.last_week,
        "planned": totals, "fixed_load": {k: round(v, 1) for k, v in fixed.items()},
        "planned_load": round(totals["total"] + fixed["total"]),
        "intensity": {"run_minutes": run_min, "hard_minutes": hard_min,
                      "easy_pct": round((run_min - hard_min) / run_min * 100, 1) if run_min else None,
                      "easy_target_pct": easy_target},
        "games": sorted(str(g) for g in week.games), "dropped": week.dropped, "health": health,
        "return_to_play": return_block(inp, week, stage, weeks),
        "weather": weather, "attribution": ATTRIBUTION if inp.weather else None,
        "levels": {"vo2": list(inp.vo2_level), "copenhagen": list(inp.copenhagen)},
        "taper": {"tournaments": [str(d) for d in inp.tournaments]} if inp.taper else None,
        "progression": {"phase_week": inp.phase_week, "deload_week": inp.deload_week,
                        "deload_every": int(inp.p("deload_every")), "progress": inp.progress[0],
                        "why": inp.progress[1]},
        "names": {a["code"]: a["name"] for p in week.placed for a in p.addons},
        "sessions": sessions,
        "fixed": [{k: s.get(k) for k in ("date", "sport", "session_type", "planned_duration_min")} for s in inp.fixed],
    }


def return_block(inp: PlanInputs, week: Week, stage: int | None, weeks: tuple[int, int]) -> dict | None:
    """F7: where the pre-season build-up stands this week."""
    if not stage:
        return None
    nxt = None
    if stage < 3:
        nxt = f"{RETURN_ADDS[stage + 1]} from week {weeks[stage - 1]}"
    game = bool(week.games) and stage < 3
    return {"stage": stage, "week": inp.phase_week, "now": RETURN_WORDS[stage], "next": nxt,
            "sprint_scale": RETURN_SCALE[stage], "game_before_full": game}


def apply_weather(week: Week) -> dict[str, str]:
    """A7 swaps: icy or very cold days move outdoor quality work inside or drop outdoor sprints."""
    p = week.inp.params.get("ANALYSIS_WEATHER", {})
    bad = {d: why for d, day in week.inp.weather.items() if (why := bad_for_outdoors(day, p))}
    for pl in list(week.placed):
        why = bad.get(week.dates[pl.day])
        if not why or not pl.entry["outdoor"]:
            continue
        if pl.entry["weather_swap"] == "indoor":
            pl.notes.append(f"forecast {why}: do it on a treadmill or bike")
        elif pl.entry["weather_swap"] == "strength":
            week.placed.remove(pl)
            week.dropped.append({"slot": pl.slot.role, "optional": pl.slot.optional,
                                 "reasons": {pl.code: [f"forecast {why}: no outdoor sprinting"]}})
    return {str(d): why for d, why in sorted(bad.items())}


def _fixed_load(s: dict, factor: float) -> float:
    return (s.get("planned_rpe") or 0) * (s.get("planned_duration_min") or 0) * factor


def plan_caps(inp: PlanInputs) -> dict[str, float]:
    """The same ramp guards the rules use (D1, A6), applied to next week."""
    run = inp.params.get("TRAIN_RUN_RAMP", {})
    ch = inp.params.get("TRAIN_CHANNEL_RAMP", {})
    total_pct = inp.params.get("TRAIN_LOAD_RAMP", {}).get("max_weekly_increase_pct", 10)
    lw = inp.last_week
    return {
        "run": ramp_cap(lw.get("run"), run.get("max_weekly_increase_pct", 10), run.get("low_base_allowance", 60)),
        "sprint": ramp_cap(lw.get("sprint"), ch.get("sprint_increase_pct", 15), ch.get("sprint_allowance_m", 100)),
        "plyo": ramp_cap(lw.get("plyo"), ch.get("plyo_increase_pct", 15), ch.get("plyo_allowance", 40)),
        "total": ramp_cap(lw.get("total"), total_pct, inp.p("total_allowance")),
    }


def load_target(inp: PlanInputs) -> tuple[float | None, str]:
    if inp.chronic_week is None:
        return None, "no chronic load yet: the ramp caps alone size the week"
    if inp.phase == "deload":
        return inp.chronic_week * inp.p("deload_factor"), "holiday deload: reduced volume"
    if inp.deload_week:
        n = int(inp.p("deload_every"))
        nth = f"{n}{'nd' if n == 2 else 'rd' if n == 3 else 'th'}"
        return inp.chronic_week * inp.p("deload_factor"), f"a deload week: every {nth} week of a phase is lighter"
    if inp.taper:
        days = ", ".join(f"{d:%a}" for d in inp.tournaments)
        return inp.chronic_week * inp.p("taper_factor"), f"a taper week for the tournament ({days})"
    if inp.caution:
        return inp.chronic_week * inp.p("caution_factor"), f"recent recovery flags ({', '.join(inp.caution)})"
    if not inp.progress[0]:
        return inp.chronic_week, inp.progress[1]
    return inp.chronic_week * inp.p("progression_factor"), inp.progress[1]


# ------------------------------------------------------------------ from the database
def vo2_level(conn: psycopg.Connection, week_start: date, phase: str, caution: bool,
              deload: bool = False) -> tuple[int, str]:
    """Step up a VO2 level after a week whose VO2 sessions were all done, unless recovery flagged or deloading."""
    rows = fetch_all(conn, """SELECT ps.date, ps.session_code, ps.status FROM core.planned_session ps
                              JOIN analytics.week_plan wp ON wp.id = ps.plan_id
                              WHERE wp.superseded_at IS NULL AND ps.session_code LIKE 'vo2\\_%%'
                                AND ps.date BETWEEN %s AND %s ORDER BY ps.date""",
                     (week_start - timedelta(days=28), week_start - timedelta(days=1)))
    if not rows:
        return 1, "starting at 3 x 3"
    levels = {v: k for k, v in VO2_CODES.items()}
    last = levels.get(rows[-1]["session_code"], 1)
    recent = [r for r in rows if r["date"] >= week_start - timedelta(days=7)]
    if not recent:
        return last, "no VO2 session last week: repeating the level"
    if phase == "deload" or deload:
        return last, "holding the level through the deload"
    if caution:
        return last, "holding the level: recent recovery flags"
    if all(r["status"] in ("done", "modified") for r in recent):
        return (last + 1, "last week's VO2 sessions done") if last < 4 else (4, "at 4 x 4")
    return last, "last week's VO2 session wasn't done: repeating it"


def gather(conn: psycopg.Connection, week_start: date) -> PlanInputs:
    profile = load_profile(conn)
    prefs, tz = profile.prefs, profile.timezone
    params = load_params(conn)
    as_of = week_start - timedelta(days=1)
    end = week_start + timedelta(days=6)
    fixed = fetch_all(conn, """SELECT * FROM core.planned_session WHERE date BETWEEN %s AND %s AND status <> 'skipped'
                               AND NOT (origin = 'generated' AND status = 'planned') ORDER BY date, id""",
                      (week_start, end))
    for s in fixed:
        if s["session_type"] == "game":
            s["planned_rpe"] = s["planned_rpe"] or 8
            s["planned_duration_min"] = s["planned_duration_min"] or 90

    def game_on(d: date) -> bool:
        return bool(fetch_one(conn, """SELECT 1 FROM core.planned_session WHERE date = %s AND session_type = 'game'
                                       AND status <> 'skipped'""", (d,)))

    f = fetch_one(conn, "SELECT * FROM analytics.daily_features WHERE date <= %s ORDER BY date DESC LIMIT 1",
                  (as_of,)) or {}
    flags = fetch_all(conn, """SELECT DISTINCT rule_id FROM analytics.daily_flag WHERE fired AND date BETWEEN %s AND %s
                               AND rule_id = ANY(%s) AND (details->>'eval_status') = 'evaluated'
                               AND coalesce(severity, 0) >= 2 ORDER BY rule_id""",
                      (week_start - timedelta(days=4), as_of, list(CAUTION_RULES)))
    caution = [r["rule_id"] for r in flags]
    injuries = load_injuries(conn, as_of)
    base = day_state(conn, as_of, tz, injuries)
    if base.hrv_status == "below":
        caution.append("HRV below its band")
    shoulder = shoulder_state(conn, as_of, tz)["flagged"]
    states = {}
    for i in range(7):
        d = week_start + timedelta(days=i)
        states[d] = DayState(date=d, phase=phase_for(conn, d), hrv_status=base.hrv_status if i < 2 else None,
                             restrictions=active_restrictions(injuries, d), tag_days=base.tag_days,
                             shoulder_flagged=shoulder)
    phase = states[week_start].phase
    week_no = phase_week(phases(conn), week_start)
    stage, weeks = return_stage(phase, week_no, params), return_weeks(params)
    if stage:
        states = {d: replace(st, return_stage=stage, return_weeks=weeks) if st.phase == "preseason" else st
                  for d, st in states.items()}
    plan_p = params.get("ANALYSIS_PLAN", {})
    deload = scheduled_deload(phase, week_no, plan_p.get("deload_every", DEFAULTS["deload_every"]))
    main = tuple(DAY_NAMES.index(x) for x in prefs.get("main_days", DAY_NAMES[:4]) if x in DAY_NAMES)
    bonus = tuple(i for i in range(7) if i not in main) if prefs.get("bonus_days", True) else ()
    goals = [r["metric"] for r in fetch_all(conn, """SELECT DISTINCT metric FROM core.goal
                                                     WHERE active AND metric IS NOT NULL ORDER BY metric""")]
    return PlanInputs(
        week_start=week_start, phase=phase, states=states, fixed=fixed,
        prev_game=game_on(as_of), next_game=game_on(end + timedelta(days=1)),
        main_days=main or (0, 1, 2, 3), bonus_days=bonus,
        day_minutes=int(prefs.get("day_minutes", 120)), bonus_minutes=int(prefs.get("bonus_minutes", 45)),
        last_week={"run": f.get("run_load_week"), "sprint": f.get("sprint_week"), "plyo": f.get("plyo_week"),
                   "total": f.get("load_week")},
        chronic_week=f["chronic_ewma"] * 7 if f.get("chronic_ewma") is not None else None,
        caution=caution, vo2_level=vo2_level(conn, week_start, phase, bool(caution), deload),
        copenhagen=copenhagen_level(conn, week_start, tz), goals=goals, params=params, factor=current_factor(conn),
        weather=daily_forecast(conn, week_start, end), phase_week=week_no,
        sleep=gather_sleep(conn, f), protein=gather_protein(conn, as_of),
        progress=progression_check(conn, week_start, plan_p.get("progress_key_pct", DEFAULTS["progress_key_pct"])))


def inputs_snapshot(inp: PlanInputs) -> dict:
    return to_jsonable({
        "planner_version": PLANNER_VERSION, "phase": inp.phase, "main_days": inp.main_days, "bonus_days": inp.bonus_days,
        "day_minutes": inp.day_minutes, "bonus_minutes": inp.bonus_minutes, "last_week": inp.last_week,
        "chronic_week": inp.chronic_week, "caution": inp.caution, "vo2_level": inp.vo2_level,
        "copenhagen": inp.copenhagen, "goals": inp.goals, "calibration_factor": inp.factor,
        "prev_game": inp.prev_game, "next_game": inp.next_game,
        "phase_week": inp.phase_week, "progress": inp.progress, "sleep": inp.sleep, "protein": inp.protein,
        "days": {str(d): s.as_dict() for d, s in inp.states.items()},
        "fixed": [{k: s.get(k) for k in ("date", "sport", "session_type", "planned_duration_min", "origin")}
                  for s in inp.fixed],
        "params": {k: v for k, v in inp.params.items()
                   if k in ("ANALYSIS_PLAN", "TRAIN_RUN_RAMP", "TRAIN_CHANNEL_RAMP", "TRAIN_LOAD_RAMP", "TRAIN_INTENSITY_MIX",
                            "ANALYSIS_WEATHER")},
        "weather": {str(d): {k: w.get(k) for k in ("tmin_c", "tmax_c", "precip_mm", "snowfall_cm", "weather_code",
                                                   "source")} for d, w in sorted(inp.weather.items())},
    })


def plan_week(conn: psycopg.Connection, week_start: date) -> dict:
    inp = gather(conn, week_start)
    plan = build_plan(inp, load_library(conn), exercise_meta(conn))
    plan["inputs"] = inputs_snapshot(inp)
    return plan


def plan_hash(plan: dict) -> str:
    keep = {k: plan[k] for k in ("phase", "target_load", "caps", "planned", "dropped", "sessions", "inputs")}
    return hashlib.sha256(json.dumps(to_jsonable(keep), sort_keys=True).encode()).hexdigest()[:16]


def render_plan(plan: dict) -> str:
    phase = PHASE_WORDS.get(plan["phase"], plan["phase"].replace("_", " "))
    caps, tot = plan["caps"], plan["planned"]
    prog = plan.get("progression") or {}
    week_no = f", week {prog['phase_week']}" if prog.get("phase_week") else ""
    deload = "; a deload week" if prog.get("deload_week") else "; a taper week" if plan.get("taper") else ""
    lines = [f"**Plan for the week of {plan['week_start']:%b %d}** ({phase} phase{week_no}{deload})", ""]
    if plan["target_load"] is not None:
        lines.append(f"Target load {plan['target_load']} (your weekly chronic load is {plan['chronic_weekly_load']}; "
                     f"{plan['reason']}). Planned: {plan['planned_load']}.")
    else:
        lines.append(f"{plan['reason'].capitalize()}. Planned load: {plan['planned_load']}.")
    lines.append(f"Ramp caps: running load {round(caps['run'])} (planned {round(tot['run'] + plan['fixed_load']['run'])}), sprinting "
                 f"{round(caps['sprint'])} m (planned {tot['sprint_m']}), jumps {round(caps['plyo'])} contacts "
                 f"(planned {tot['plyo_contacts']}).")
    lines.append("")
    by_day: dict[date, list[str]] = {}
    for s in plan["fixed"]:
        label = "ultimate game" if s["session_type"] == "game" else f"{s['session_type']} {s['sport']}"
        by_day.setdefault(s["date"], []).append(label)
    names = plan.get("names", {})
    for s in plan["sessions"]:
        extra = f" + {', '.join(names.get(a, a.replace('_', ' ')) for a in s['addons'])}" if s["addons"] else ""
        by_day.setdefault(s["date"], []).append(f"{s['notes']} ({s['planned_duration_min']} min){extra}")
    optional = {s["date"] for s in plan["sessions"] if s["optional"]}
    for d in sorted(by_day):
        lines.append(f"- {d:%a}{' (optional)' if d in optional else ''}: " + "; ".join(by_day[d]))
    left_out = [x["slot"].replace("_", " ") for x in plan["dropped"] if not x["optional"]]
    if left_out:
        lines += ["", f"Left out this week: {', '.join(left_out)} (see each session's reason)."]
    rtp = plan.get("return_to_play")
    if rtp:
        line = f"Return to ultimate: week {rtp['week']} of pre-season, {rtp['now']}"
        if rtp["next"]:
            line += f"; {rtp['next']}"
        if rtp["sprint_scale"] < 1:
            line += f". Sprint volume at {round(rtp['sprint_scale'] * 100)}%"
        lines += ["", line + "."]
        if rtp["game_before_full"]:
            lines.append("- A game this week comes before the build-up is complete: ease into the first points.")
    health = render_health(plan.get("health"))
    if health:
        lines += ["", *health]
    if plan.get("weather"):
        days = "; ".join(f"{date.fromisoformat(d):%a} {why}" for d, why in plan["weather"].items())
        lines += ["", f"Weather: {days}. Outdoor intervals move to a treadmill or bike; outdoor sprints wait."]
    if plan.get("attribution"):
        lines += ["", f"_{plan['attribution']}_"]
    return "\n".join(lines)


def write_week_plan(conn: psycopg.Connection, week_start: date) -> int:
    """Generate next week's plan. A new version only when the plan changed."""
    if not load_profile(conn).prefs.get("generate_plan", True):
        return 0
    plan = plan_week(conn, week_start)
    h = plan_hash(plan)
    cur = fetch_one(conn, "SELECT * FROM analytics.week_plan WHERE week_start = %s AND superseded_at IS NULL",
                    (week_start,))
    if cur and cur["plan_hash"] == h:
        n = fetch_one(conn, "SELECT count(*) AS n FROM core.planned_session WHERE plan_id = %s", (cur["id"],))["n"]
        if n == len(plan["sessions"]):
            return n
    if cur:
        execute(conn, "UPDATE analytics.week_plan SET superseded_at = now() WHERE id = %s", (cur["id"],))
    version = (fetch_one(conn, "SELECT max(version) AS v FROM analytics.week_plan WHERE week_start = %s",
                         (week_start,))["v"] or 0) + 1
    summary = {k: plan[k] for k in ("target_load", "reason", "chronic_weekly_load", "caps", "last_week", "planned",
                                    "fixed_load", "planned_load", "intensity", "games", "dropped", "levels", "names",
                                    "weather", "attribution", "progression", "health", "return_to_play", "taper")}
    plan_id = insert(conn, "analytics.week_plan", {
        "week_start": week_start, "version": version, "phase": plan["phase"], "inputs": plan["inputs"],
        "summary": to_jsonable(summary), "plan_hash": h})
    execute(conn, """DELETE FROM core.planned_session WHERE origin = 'generated' AND status = 'planned'
                     AND date BETWEEN %s AND %s""", (week_start, week_start + timedelta(days=6)))
    text = render_plan(plan)
    rec_id = upsert(conn, "analytics.recommendation", [{
        "date": week_start, "kind": "weekly", "input_snapshot": {"plan_inputs": plan["inputs"], "week_plan_id": plan_id},
        "decisions": to_jsonable({k: v for k, v in plan.items() if k != "inputs"}), "rendered_text": text,
        "llm_model": None, "shown_to_user": True,
    }], conflict=["date", "kind"], update=["input_snapshot", "decisions", "rendered_text", "shown_to_user"],
        returning="id")[0]
    for s in plan["sessions"]:
        insert(conn, "core.planned_session", {**s, "recommendation_id": rec_id, "plan_id": plan_id}, returning=None)
    return len(plan["sessions"])


# ------------------------------------------------------------------ grading
# Workout sports that complete a planned session: exact matches first, then the fallbacks
# (sprints recorded on the watch arrive as runs).
SPORT_MATCH = {"run": {"run", "treadmill", "trail_run"}, "speed": {"speed", "sprints", "plyometrics"},
               "strength": {"strength"}, "throwing": {"throwing", "ultimate"}}
SPORT_FALLBACK = {"speed": {"run"}}


def link_planned(conn: psycopg.Connection, start: date, end: date) -> int:
    """Mark generated sessions done when a matching workout exists (same day, compatible sport)."""
    tz = load_profile(conn).timezone
    n = 0
    for table in (SPORT_MATCH, SPORT_FALLBACK):
        planned = fetch_all(conn, """SELECT * FROM core.planned_session WHERE origin = 'generated' AND status = 'planned'
                                     AND linked_workout_id IS NULL AND date BETWEEN %s AND %s ORDER BY date, id""",
                            (start, end))
        for s in planned:
            sports = table.get(s["sport"])
            if not sports:
                continue
            w = fetch_one(conn, """SELECT w.id FROM core.v_workout w WHERE (w.start_ts AT TIME ZONE %s)::date = %s
                                   AND w.sport = ANY(%s) AND NOT w.is_game
                                   AND NOT EXISTS (SELECT 1 FROM core.planned_session p WHERE p.linked_workout_id = w.id)
                                   ORDER BY w.start_ts LIMIT 1""", (tz, s["date"], sorted(sports)))
            if w:
                n += execute(conn, "UPDATE core.planned_session SET status = 'done', linked_workout_id = %s "
                                   "WHERE id = %s", (w["id"], s["id"]))
    return n


def grade_week_plan(conn: psycopg.Connection, week_start: date) -> int:
    """After the week: adherence to the current plan version and how recovery responded."""
    plan = fetch_one(conn, "SELECT * FROM analytics.week_plan WHERE week_start = %s AND superseded_at IS NULL",
                     (week_start,))
    if not plan:
        return 0
    end = week_start + timedelta(days=6)
    link_planned(conn, week_start, end)
    rows = fetch_all(conn, "SELECT * FROM core.planned_session WHERE plan_id = %s", (plan["id"],))
    counted = [r for r in rows if not r["optional"] and r["sport"] in SPORT_MATCH]
    done = [r for r in counted if r["status"] in ("done", "modified")]
    key = [r for r in counted if r["key"]]
    status = {r["metric"]: r["status"] for r in fetch_all(
        conn, "SELECT metric, status FROM analytics.metric_status WHERE date = %s", (end,))}
    soreness = fetch_one(conn, "SELECT avg(soreness) AS s FROM core.journal WHERE date BETWEEN %s AND %s",
                         (week_start, end))["s"]
    flags = fetch_one(conn, """SELECT count(*) AS n FROM analytics.daily_flag WHERE fired AND date BETWEEN %s AND %s
                               AND rule_id = ANY(%s) AND coalesce(severity, 0) >= 2""",
                      (week_start, end, list(CAUTION_RULES)))["n"]
    actual = fetch_one(conn, "SELECT load_week FROM analytics.daily_features WHERE date = %s", (end,))
    execute(conn, """UPDATE analytics.week_plan SET graded_at = now(), adherence_pct = %s, key_done = %s,
                     key_planned = %s, load_planned = %s, load_actual = %s, response = %s WHERE id = %s""",
            (round(len(done) / len(counted) * 100, 1) if counted else None,
             sum(1 for r in key if r["status"] in ("done", "modified")), len(key),
             (plan["summary"] or {}).get("planned", {}).get("total"), actual["load_week"] if actual else None,
             json.dumps(to_jsonable({"hrv_status": status.get("ln_hrv"), "resting_hr_status": status.get("resting_hr"),
                                     "soreness_avg": round(soreness, 1) if soreness is not None else None,
                                     "recovery_flags": flags})), plan["id"]))
    return 1
