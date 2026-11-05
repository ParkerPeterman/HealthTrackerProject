"""Phase templates (F3): which library sessions (F2) a week of each phase is built from.

A slot names a role, the day it prefers, the sessions that can fill it in order of
preference, the add-ons done the same day, and how the placer treats it:

  day       0-3: the main days (Monday to Thursday by default); 4-6: the optional
            Friday-Sunday bonus slots
  key       the week's priority sessions: placed first, trimmed last
  move      when the preferred day doesn't work, try the slot's first session on other main
            days before falling back to the next candidate. Monday sprints don't move: after
            a weekend game they become sprint technique instead.
  optional  a bonus slot on a travel day: short and equipment-light
  budget    minutes allowed on a bonus day for this slot (the weekend long run in VO2 build)
  repeat    may reuse a session already in the week (two VO2 sessions in VO2 build)

Tokens stand for this week's progression or rotation:
  {vo2}         the VO2 level (3 x 3 -> 4 x 3 -> 3 x 4 -> 4 x 4)
  {copenhagen}  the Copenhagen level (1-3)
  {speed}       acceleration and max-velocity sprints on alternate weeks
  {speed_pre}   max-velocity sprints and change of direction on alternate weeks
  {ultimate}    repeated sprints and game-like intervals on alternate weeks

In pre-season the return-to-ultimate stage (F7) decides which of these fit: in weeks 1-2
the Monday slot falls back to acceleration sprints and the Thursday one to hill repeats.

The base template is the brief's: Monday speed and jumps then heavy legs with Copenhagen
and Nordics, Tuesday easy run and upper body with shoulder work, Wednesday VO2, Thursday
easy long run with strides and single-leg strength, optional travel-day sessions. VO2 build
puts its two VO2 sessions on Tuesday and Thursday so they are never back to back, with the
long run moved to an optional weekend slot.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    role: str
    day: int
    candidates: tuple[str, ...]
    addons: tuple[str, ...] = ()
    key: bool = False
    move: bool = True
    optional: bool = False
    budget: int | None = None
    repeat: bool = False
    why: str = ""


SPEED = Slot("speed", 0, ("{speed}", "speed_technique"), key=True, move=False,
             why="Speed work fresh after the weekend")
JUMPS = Slot("jumps", 0, ("jump_cmj_broad",), move=False, why="Jumps before lifting")
BONUS_PREHAB = Slot("bonus_prehab", 6, ("mobility",), addons=("shoulder_bands",), optional=True,
                    why="Optional on a travel day: mobility and shoulder bands")

TEMPLATES: dict[str, tuple[Slot, ...]] = {
    "base": (
        SPEED,
        JUMPS,
        Slot("lower", 0, ("strength_lower_heavy", "strength_single_leg"), addons=("{copenhagen}", "nordics"),
             why="Heavy lower-body strength after sprints and jumps; Copenhagen and Nordics for groin and hamstrings"),
        Slot("easy", 1, ("easy_run",), repeat=True, why="Easy aerobic volume"),
        Slot("upper", 1, ("strength_upper",), addons=("shoulder_bands",),
             why="Upper-body push and pull, plus rotator cuff and scapular work"),
        Slot("vo2", 2, ("{vo2}", "tempo_run"), key=True, why="One VO2 session a week, building from 3 x 3 to 4 x 4"),
        Slot("long", 3, ("long_run", "easy_run"), addons=("strides",), key=True, repeat=True,
             why="Easy long run for aerobic durability, with strides"),
        Slot("single_leg", 3, ("strength_single_leg",), addons=("balance_ankle",), why="Single-leg strength and balance"),
        Slot("bonus_run", 5, ("easy_run",), optional=True, repeat=True, why="Optional easy run on a travel day"),
        BONUS_PREHAB,
    ),
    "deload": (
        Slot("single_leg", 0, ("strength_single_leg",), addons=("{copenhagen}", "nordics"),
             why="Holiday deload: lighter strength, keep the injury-prevention work"),
        Slot("easy", 1, ("easy_run",), addons=("strides",), key=True, repeat=True, why="Holiday deload: easy running only"),
        Slot("upper", 2, ("strength_upper",), addons=("shoulder_bands",), why="Holiday deload: lighter upper body"),
        Slot("easy_2", 3, ("easy_run",), repeat=True, why="Holiday deload: easy running only"),
        BONUS_PREHAB,
    ),
    "vo2_build": (
        SPEED,
        JUMPS,
        Slot("power", 0, ("strength_power", "strength_lower_heavy", "strength_single_leg"),
             addons=("{copenhagen}", "nordics"), why="Strength shifting toward power"),
        Slot("vo2", 1, ("{vo2}", "tempo_run"), key=True, why="First of two VO2 sessions this week"),
        Slot("easy", 2, ("easy_run",), repeat=True, why="Easy aerobic volume between the VO2 sessions"),
        Slot("upper", 2, ("strength_upper",), addons=("shoulder_bands",),
             why="Upper-body push and pull, plus rotator cuff and scapular work"),
        Slot("vo2_2", 3, ("{vo2}", "hill_repeats"), key=True, repeat=True,
             why="Second VO2 session; Tuesday and Thursday keep the two apart"),
        Slot("single_leg", 3, ("strength_single_leg",), addons=("balance_ankle",), why="Single-leg strength and balance"),
        Slot("long", 5, ("long_run",), addons=("strides",), optional=True, budget=75,
             why="In VO2 build the long run moves to an optional weekend slot"),
        BONUS_PREHAB,
    ),
    "preseason": (
        Slot("speed", 0, ("{speed_pre}", "accel_sprints", "speed_technique"), key=True, move=False,
             why="Top speed and change of direction, fresh after the weekend"),
        Slot("jumps", 0, ("depth_jumps", "jump_cmj_broad"), move=False, why="Plyometric progression: depth jumps"),
        Slot("power", 0, ("strength_power", "strength_lower_heavy"), addons=("{copenhagen}", "nordics"),
             why="Power, with Copenhagen (three times a week in pre-season) and Nordics"),
        Slot("vo2", 1, ("{vo2}",), key=True, why="One VO2 session a week"),
        Slot("throwing", 1, ("throwing",), why="Throwing volume, counted toward shoulder load"),
        Slot("easy", 2, ("easy_run",), repeat=True, why="Easy aerobic volume"),
        Slot("upper", 2, ("strength_upper",), addons=("shoulder_bands", "{copenhagen}"),
             why="Upper body, shoulder work, and the second Copenhagen session"),
        Slot("ultimate", 3, ("{ultimate}", "cod_drills", "hill_repeats"), key=True,
             why="Repeated sprints and game-like intervals for points"),
        Slot("single_leg", 3, ("strength_single_leg",), addons=("{copenhagen}",),
             why="Single-leg strength and the third Copenhagen session"),
        Slot("bonus_run", 5, ("easy_run",), optional=True, repeat=True, why="Optional easy run on a travel day"),
        BONUS_PREHAB,
    ),
    "in_season": (
        Slot("strength_1", 0, ("strength_maintenance", "strength_upper"), addons=("{copenhagen}", "nordics"), key=True,
             why="Maintenance strength; Copenhagen once a week in-season"),
        Slot("quality", 1, ("{vo2}", "game_like_intervals"), why="One quality session when the games leave room"),
        Slot("throwing", 1, ("throwing",), why="Throwing volume, counted toward shoulder load"),
        Slot("easy", 2, ("easy_run",), addons=("shoulder_bands",), repeat=True, why="Easy recovery run and shoulder work"),
        Slot("strength_2", 3, ("strength_maintenance", "strength_upper"), addons=("shoulder_bands",), repeat=True,
             why="Second maintenance strength session"),
        BONUS_PREHAB,
    ),
    "transition": (
        Slot("easy", 1, ("easy_run",), optional=True, repeat=True, why="Transition: unstructured and low load"),
        Slot("mobility", 2, ("mobility",), addons=("shoulder_bands",), optional=True,
             why="Transition: unstructured and low load"),
        Slot("easy_2", 3, ("easy_run",), optional=True, repeat=True, why="Transition: unstructured and low load"),
    ),
}

# Goals that make a slot a key session (goals/metrics.py keys).
GOAL_ROLES = {"sprint_10m_s": "speed", "sprint_30m_s": "speed", "cmj_cm": "jumps", "broad_jump_cm": "jumps",
              "vo2max": "vo2", "run_5k_min": "vo2", "pace_at_ref_hr_s_km": "long"}
ROTATIONS = {"{speed}": ("accel_sprints", "maxv_sprints"), "{speed_pre}": ("maxv_sprints", "cod_drills"),
             "{ultimate}": ("repeated_sprints", "game_like_intervals")}
