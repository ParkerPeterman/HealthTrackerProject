"""Synthetic athlete for demos, tests, and screenshots. No real health data.

Garmin data is written as raw payloads shaped like Garmin Connect's API, so
the real parsers run on it. App data (food, lifts, games, journal, injuries)
goes straight into core, as the tracker app would write it.

Planted effects, so the weekly stats job has something true to find:
  * caffeine at/after 14:00      -> that night's sleep score -7, sleep -20 min
  * each alcoholic drink         -> next-morning HRV -6%, resting HR +1.5
  * last meal at/after 21:00     -> sleep score -4
  * an ultimate game             -> HRV -12% next morning, -5% the morning after
  * sleep under 6.5 h            -> energy -1 that day
  * a 5-day illness, 3 days of travel, an ankle sprain with rehab, a bench plateau
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import psycopg

from healthtracker.db import execute, fetch_all, insert, upsert
from healthtracker.ingest.raw import store_raw

TZ = ZoneInfo("America/Chicago")
MILE = 1609.344

FOODS = [
    # name, serving, g, kcal, protein, carbs, fat, fiber, sugar, sodium, caffeine, alcohol, micros
    ("Rolled oats", "1/2 cup dry", 40, 150, 5, 27, 3, 4, 1, 0, 0, 0, {"iron_mg": 1.7, "magnesium_mg": 56}),
    ("Banana", "1 medium", 118, 105, 1.3, 27, 0.4, 3.1, 14, 1, 0, 0, {"magnesium_mg": 32, "potassium_mg": 422}),
    ("Greek yogurt, plain nonfat", "170 g cup", 170, 100, 17, 6, 0, 0, 6, 60, 0, 0, {"calcium_mg": 190}),
    ("Whey protein", "1 scoop", 31, 120, 24, 3, 1.5, 0, 2, 130, 0, 0, {"calcium_mg": 120}),
    ("Coffee, brewed", "12 oz", 355, 5, 0.4, 0, 0, 0, 0, 7, 140, 0, {"magnesium_mg": 11}),
    ("Cold brew", "16 oz", 473, 10, 0.5, 1, 0, 0, 0, 15, 200, 0, {}),
    ("Eggs", "2 large", 100, 143, 12.6, 0.7, 9.5, 0, 0.4, 142, 0, 0, {"iron_mg": 1.8}),
    ("Whole wheat toast", "2 slices", 64, 160, 8, 28, 2, 4, 4, 280, 0, 0, {"iron_mg": 1.6, "magnesium_mg": 48}),
    ("Chicken breast, cooked", "6 oz", 170, 280, 53, 0, 6, 0, 0, 125, 0, 0, {"iron_mg": 1.0, "magnesium_mg": 50}),
    ("White rice, cooked", "1.5 cups", 237, 310, 6, 68, 0.6, 0.9, 0, 3, 0, 0, {"iron_mg": 0.6}),
    ("Broccoli", "1 cup", 91, 31, 2.5, 6, 0.3, 2.4, 1.5, 30, 0, 0, {"iron_mg": 0.7, "magnesium_mg": 19}),
    ("Ground beef 90/10, cooked", "5 oz", 142, 300, 37, 0, 16, 0, 0, 100, 0, 0, {"iron_mg": 3.6, "zinc_mg": 8}),
    ("Pasta, cooked", "2 cups", 280, 440, 16, 86, 2.6, 5, 1.6, 2, 0, 0, {"iron_mg": 1.8}),
    ("Marinara sauce", "1/2 cup", 125, 70, 2, 10, 2.5, 2, 6, 450, 0, 0, {}),
    ("Salmon, cooked", "6 oz", 170, 350, 38, 0, 21, 0, 0, 100, 0, 0, {"vitamin_d_mcg": 14, "omega3_g": 2.5}),
    ("Sweet potato", "1 medium", 150, 130, 2.4, 30, 0.2, 4.5, 9, 60, 0, 0, {"potassium_mg": 540}),
    ("Black beans", "1 cup", 172, 227, 15, 41, 0.9, 15, 0.6, 2, 0, 0, {"iron_mg": 3.6, "magnesium_mg": 120}),
    ("Tortillas, flour", "2 medium", 90, 280, 7, 46, 7, 2, 2, 600, 0, 0, {"iron_mg": 2.6}),
    ("Cheddar cheese", "1 oz", 28, 115, 7, 0.4, 9.5, 0, 0.1, 180, 0, 0, {"calcium_mg": 200}),
    ("Peanut butter", "2 tbsp", 32, 190, 7, 7, 16, 2, 3, 140, 0, 0, {"magnesium_mg": 49}),
    ("Apple", "1 medium", 182, 95, 0.5, 25, 0.3, 4.4, 19, 2, 0, 0, {}),
    ("Almonds", "1 oz", 28, 165, 6, 6, 14, 3.5, 1.2, 0, 0, 0, {"magnesium_mg": 76}),
    ("Protein bar", "1 bar", 60, 210, 20, 23, 7, 5, 6, 200, 0, 0, {}),
    ("Pizza", "2 slices", 214, 570, 24, 70, 21, 4, 8, 1280, 0, 0, {"calcium_mg": 400}),
    ("Burrito bowl", "1 bowl", 550, 780, 45, 85, 26, 16, 6, 1900, 0, 0, {"iron_mg": 5}),
    ("Beer, IPA", "12 oz", 355, 200, 2, 16, 0, 0, 0, 14, 0, 18, {}),
    ("Beer, light", "12 oz", 355, 103, 1, 6, 0, 0, 0, 14, 0, 11, {}),
    ("Spinach salad", "large", 150, 120, 5, 10, 7, 4, 3, 220, 0, 0, {"iron_mg": 3.0, "magnesium_mg": 90}),
    ("Milk, 2%", "1 cup", 244, 122, 8, 12, 5, 0, 12, 115, 0, 0, {"calcium_mg": 293, "vitamin_d_mcg": 3}),
    ("Energy drink", "16 oz can", 473, 10, 0, 3, 0, 0, 0, 200, 160, 0, {}),
    ("Trail mix", "1/3 cup", 50, 230, 6, 22, 14, 2.5, 13, 60, 0, 0, {"magnesium_mg": 60}),
    ("Bagel with cream cheese", "1", 140, 380, 12, 56, 11, 2, 7, 600, 0, 0, {"iron_mg": 3.5}),
    ("Turkey sandwich", "1", 250, 450, 30, 45, 15, 5, 6, 1300, 0, 0, {"iron_mg": 3}),
    ("Chocolate milk", "16 oz", 480, 380, 16, 60, 10, 2, 56, 300, 6, 0, {"calcium_mg": 560}),
]


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def local(d: date, hh: float) -> datetime:
    """Local datetime on date d at decimal hour hh (may exceed 24)."""
    base = datetime.combine(d, time(0), TZ)
    return base + timedelta(hours=hh)


def gmt_str(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class DayPlan:
    d: date
    game: list[tuple[float, int, int]] = field(default_factory=list)  # (start hour, minutes, rpe)
    run: str | None = None  # easy | tempo | long
    lift: str | None = None  # lower | upper
    sick: bool = False
    travel: bool = False
    caffeine_late: float | None = None
    drinks: int = 0
    late_meal: bool = False


class Generator:
    def __init__(self, conn: psycopg.Connection, days: int = 180, end: date | None = None, seed: int = 42):
        self.conn = conn
        self.rng = np.random.default_rng(seed)
        self.end = end or datetime.now(TZ).date()
        self.start = self.end - timedelta(days=days - 1)
        self.days = days
        self.food_ids: dict[str, int] = {}
        self.ex_ids: dict[str, int] = {}
        self.activity_id = 21_000_000_000
        self.injury_id: int | None = None

    # ------------------------------------------------------------ setup
    def seed_reference(self) -> None:
        c = self.conn
        execute(c, """UPDATE core.profile SET birth_date = '1999-05-01', sex = 'male', height_cm = 180,
                      timezone = 'America/Chicago', ref_hr_bpm = 145, display_units = 'imperial'
                      WHERE id = 1""")
        insert(c, "core.goal", {"goal_type": "body_weight", "description": "Lean out to 175 lb for the season",
                                "target_value": 79.4, "unit": "kg", "target_date": self.end + timedelta(days=60)},
               returning=None)
        insert(c, "core.goal", {"goal_type": "performance", "description": "Sub-21 5K", "target_value": 21,
                                "unit": "min", "target_date": self.end + timedelta(days=120)}, returning=None)
        for nutrient, basis, ev, lo, hi, ref in [
            ("kcal", "per_day", "daily", 2700, 3000, "demo goal"),
            ("fiber_g", "per_day", "weekly_avg", 38, None, "DRI adequate intake, men 19-50"),
            ("magnesium_mg", "per_day", "weekly_avg", 400, None, "RDA, men 19-30"),
            ("iron_mg", "per_day", "weekly_avg", 8, None, "RDA, men 19+"),
        ]:
            insert(c, "core.nutrient_target", {"nutrient": nutrient, "basis": basis, "evaluation": ev,
                                               "min_value": lo, "max_value": hi, "source_ref": ref,
                                               "valid_from": self.start}, returning=None)
        for f in FOODS:
            name, sdesc, g, kcal, p, cb, fat, fib, sug, na, caf, alc, micros = f
            self.food_ids[name] = insert(c, "core.food_catalog", {
                "name": name, "serving_desc": sdesc, "serving_g": g, "kcal": kcal, "protein_g": p,
                "carbs_g": cb, "fat_g": fat, "fiber_g": fib, "sugar_g": sug, "sodium_mg": na,
                "caffeine_mg": caf, "alcohol_g": alc, "micros": micros, "origin": "manual", "verified": True})
        # The exercise library is seeded by migration 003.
        self.ex_ids = {r["name"]: r["id"] for r in fetch_all(c, "SELECT id, name FROM core.exercise")}
        sm = insert(c, "core.saved_meal", {"name": "Usual breakfast"})
        for item, servings in (("Rolled oats", 2), ("Banana", 1), ("Whey protein", 1), ("Coffee, brewed", 1)):
            insert(c, "core.saved_meal_item", {"saved_meal_id": sm, "catalog_id": self.food_ids[item],
                                               "servings": servings}, returning=None)

    # ------------------------------------------------------------ schedule
    def plan(self) -> list[DayPlan]:
        rng = self.rng
        plans = []
        ill_start = self.days * 55 // 100
        travel_start = self.days * 78 // 100
        self.sprain_day = self.days * 62 // 100
        while (self.start + timedelta(days=self.sprain_day)).weekday() != 1:  # a Tuesday league game
            self.sprain_day += 1
        for i in range(self.days):
            d = self.start + timedelta(days=i)
            p = DayPlan(d)
            wd = d.weekday()
            week = i // 7
            p.sick = ill_start <= i < ill_start + 5
            p.travel = travel_start <= i < travel_start + 3
            injured = self.sprain_day < i < self.sprain_day + 24
            if not p.sick and not p.travel:
                if wd == 1 and not (self.sprain_day < i < self.sprain_day + 21):
                    p.game.append((19.0, 90, int(rng.integers(7, 10))))
                if wd == 5 and week % 3 == 1 and not injured:
                    p.game += [(9.0, 80, 8), (11.5, 80, 9)]
                if wd == 0:
                    p.run = "easy"
                elif wd == 3:
                    p.run = "easy" if injured else "tempo"
                elif wd == 6:
                    p.run = None if (injured and i < self.sprain_day + 14) else "long"
                if wd == 2:
                    p.lift = "upper" if injured and i < self.sprain_day + 10 else "lower"
                elif wd == 4:
                    p.lift = "upper"
                if injured and i < self.sprain_day + 10:
                    p.run = None
            p.caffeine_late = float(rng.choice([13.5, 14.5, 15.5, 16.5])) if rng.random() < 0.3 else None
            if wd in (4, 5) and rng.random() < 0.55:
                p.drinks = int(rng.choice([1, 2, 2, 3, 4]))
            elif rng.random() < 0.07:
                p.drinks = 1
            if p.sick:
                p.drinks, p.caffeine_late = 0, None
            p.late_meal = bool(rng.random() < (0.45 if p.game and p.game[0][0] >= 19 else 0.15))
            plans.append(p)
        return plans

    # ------------------------------------------------------------ generate
    def run(self) -> dict:
        self.seed_reference()
        plans = self.plan()
        rng = self.rng
        c = self.conn
        counts = {"raw": 0, "workouts": 0, "food_entries": 0, "sets": 0}
        hrv_level, rhr_level, weight = 0.0, 0.0, 82.0
        prev: DayPlan | None = None
        prev2: DayPlan | None = None
        sleep_debt = 0.0
        hr_max = 194
        for i, p in enumerate(plans):
            d = p.d
            progress = i / max(1, self.days - 1)
            # ---------------- overnight (night ending on d) driven by yesterday
            hrv_level = 0.75 * hrv_level + rng.normal(0, 0.06)
            rhr_level = 0.7 * rhr_level + rng.normal(0, 1.2)
            hrv_mult = math.exp(hrv_level) * (1 + 0.05 * progress)
            rhr = 51 - 2 * progress + rhr_level
            score = 80 + rng.normal(0, 5)
            total = 455 + rng.normal(0, 28)
            bed_hour = 22.9 + rng.normal(0, 0.35)
            if prev:
                if prev.game:
                    hrv_mult *= 0.88
                    rhr += 3
                    bed_hour += 0.6
                if prev.drinks:
                    hrv_mult *= (1 - 0.06 * prev.drinks)
                    rhr += 1.5 * prev.drinks
                    score -= 3 * prev.drinks
                if prev.caffeine_late and prev.caffeine_late >= 14:
                    score -= 7
                    total -= 20
                if prev.late_meal:
                    score -= 4
                if prev.d.weekday() in (4, 5):
                    bed_hour += 0.8
                    total -= 25
                if prev.travel:
                    total -= 40
                    score -= 6
            if prev2 and prev2.game:
                hrv_mult *= 0.95
            if p.sick or (prev and prev.sick):
                hrv_mult *= 0.75
                rhr += 7
                score -= 10
            hrv = 62 * hrv_mult
            total = float(np.clip(total, 300, 560))
            score = int(np.clip(score + (total - 450) / 10, 35, 99))
            start_dt = local(d, bed_hour - 24)
            end_dt = start_dt + timedelta(minutes=total + 18)
            sleep_payload = {
                "dailySleepDTO": {
                    "calendarDate": d.isoformat(), "sleepTimeSeconds": int(total * 60),
                    "sleepStartTimestampGMT": ms(start_dt), "sleepEndTimestampGMT": ms(end_dt),
                    "deepSleepSeconds": int(total * 60 * 0.18), "remSleepSeconds": int(total * 60 * 0.22),
                    "lightSleepSeconds": int(total * 60 * 0.56), "awakeSleepSeconds": int(18 * 60),
                    "averageSpO2Value": round(95 + rng.normal(0, 0.8), 1),
                    "averageRespirationValue": round(14 + rng.normal(0, 0.6) + (2 if p.sick else 0), 1),
                    "sleepScores": {"overall": {"value": score, "qualifierKey": "GOOD" if score >= 80 else "FAIR"}},
                },
                "avgOvernightHrv": round(hrv, 1), "hrvStatus": "BALANCED" if hrv > 50 else "UNBALANCED",
                "restingHeartRate": int(round(rhr)),
            }
            counts["raw"] += store_raw(c, "garmin", "sleep", d, sleep_payload)
            counts["raw"] += store_raw(c, "garmin", "hrv", d, {"hrvSummary": {
                "calendarDate": d.isoformat(), "lastNightAvg": round(hrv), "status": sleep_payload["hrvStatus"],
                "weeklyAvg": round(62 * (1 + 0.05 * progress))}})
            sleep_debt = 0.7 * sleep_debt + max(0.0, 420 - total)

            # ---------------- daytime
            sessions = []  # (start dt, minutes, avg hr, kind)
            for (h, mins, rpe) in p.game:
                start = local(d, h)
                wid = insert(c, "core.workout", {"source": "manual", "sport": "ultimate", "is_game": True,
                                                 "start_ts": start, "duration_s": mins * 60, "rpe": rpe,
                                                 "rpe_source": "manual",
                                                 "notes": "tournament" if len(p.game) > 1 else "league game"})
                insert(c, "core.planned_session", {"date": d, "sport": "ultimate", "session_type": "game",
                                                   "planned_duration_min": mins, "planned_rpe": 8, "status": "done",
                                                   "linked_workout_id": wid}, returning=None)
                sessions.append((start, mins, None, "game"))
                counts["workouts"] += 1
                if i == self.sprain_day and h == p.game[0][0]:
                    self._sprain(d)
            if p.run:
                counts["raw"] += self._run(p, progress, hr_max, sessions)
                counts["workouts"] += 1
            if p.lift:
                n_sets = self._lift(p, i, sessions, garmin=(i // 7) % 2 == 0)
                counts["sets"] += n_sets
                counts["workouts"] += 1
            self._wellness(p, rhr, hrv, sessions, score)
            counts["raw"] += 5

            # ---------------- food, weight, journal, events
            counts["food_entries"] += self._food(p, weight)
            weight += -0.012 + rng.normal(0, 0.05)
            if rng.random() < 0.82 and not p.travel:
                insert(c, "core.body_measurement", {
                    "ts": local(d, 6.6 + rng.normal(0, 0.2)),
                    "weight_kg": round(weight + rng.normal(0, 0.35) + (0.4 if prev and prev.drinks else 0), 2)},
                    returning=None)
            if rng.random() < 0.85:
                energy = 3.6 + rng.normal(0, 0.6) - (1 if total < 390 else 0) - (1 if p.sick else 0) \
                    - sleep_debt / 400
                sore = 2 + (1.5 if prev and prev.game else 0) + (1 if prev and prev.lift == "lower" else 0) \
                    + rng.normal(0, 0.5)
                mood = 3.7 + rng.normal(0, 0.6) - (1 if p.sick else 0) + (0.3 if p.game else 0)
                insert(c, "core.journal", {
                    "date": d, "mood": _clip5(mood), "energy": _clip5(energy), "soreness": _clip5(sore),
                    "stress": _clip5(2.5 + rng.normal(0, 0.7)), "motivation": _clip5(3.6 + rng.normal(0, 0.7)),
                    "note": "felt flat" if energy < 2.5 else None}, returning=None)
            if p.sick:
                insert(c, "core.behavior_event", {"ts": local(d, 8), "type_code": "sick", "note": "head cold"},
                       returning=None)
            if p.travel:
                insert(c, "core.behavior_event", {"ts": local(d, 8), "type_code": "travel", "note": "work trip"},
                       returning=None)
            if rng.random() < 0.08:
                insert(c, "core.behavior_event", {"ts": local(d, 14.5), "type_code": "nap",
                                                  "value": int(rng.integers(20, 45))}, returning=None)
            insert(c, "core.behavior_event", {"ts": local(d, 7.5), "type_code": "supplement", "value": 5,
                                              "note": "creatine 5 g"}, returning=None)
            prev2, prev = prev, p

        self._future_games()
        self._injury_followup()
        return counts

    # ------------------------------------------------------------ pieces
    def _sprain(self, d: date) -> None:
        c = self.conn
        self.injury_id = insert(c, "core.injury", {
            "body_part": "ankle", "side": "left", "injury_type": "sprain", "onset_date": d,
            "mechanism": "rolled it on a cut during a game", "status": "active", "seen_professional": True,
            "notes": "grade 1, PT cleared progressive loading"})
        insert(c, "core.injury_restriction", {"injury_id": self.injury_id, "target_type": "activity",
                                              "activity": "cutting", "max_rpe": 0, "start_date": d + timedelta(days=1),
                                              "end_date": d + timedelta(days=20), "origin": "professional"},
               returning=None)
        insert(c, "core.injury_restriction", {"injury_id": self.injury_id, "target_type": "activity",
                                              "activity": "running", "max_rpe": 5,
                                              "start_date": d + timedelta(days=8),
                                              "end_date": d + timedelta(days=21), "origin": "professional"},
               returning=None)
        insert(c, "core.injury_restriction", {"injury_id": self.injury_id, "target_type": "movement_pattern",
                                              "movement_pattern": "plyometric", "max_rpe": 0, "start_date": d + timedelta(days=1),
                                              "end_date": d + timedelta(days=24), "origin": "manual"},
               returning=None)
        pain = [5, 5, 4, 4, 4, 3, 3, 3, 3, 2, 2, 3, 2, 2, 2, 1, 1, 1, 1, 1, 0, 1, 0, 0]
        for k, pa in enumerate(pain):
            insert(c, "core.injury_checkin", {
                "date": d + timedelta(days=k), "injury_id": self.injury_id, "pain_rest": max(0, pa - 2),
                "pain_activity": pa, "function_score": min(5, 2 + k // 5), "swelling": k < 3}, returning=None)

    def _injury_followup(self) -> None:
        if self.injury_id:
            execute(self.conn, "UPDATE core.injury SET status = 'resolved', resolved_date = %s WHERE id = %s",
                    (self.start + timedelta(days=self.sprain_day + 24), self.injury_id))

    def _future_games(self) -> None:
        for k in range(1, 22):
            d = self.end + timedelta(days=k)
            if d.weekday() == 1:
                insert(self.conn, "core.planned_session", {"date": d, "sport": "ultimate", "session_type": "game",
                                                           "planned_duration_min": 90, "planned_rpe": 8},
                       returning=None)

    def _run(self, p: DayPlan, progress: float, hr_max: int, sessions: list) -> int:
        rng, c = self.rng, self.conn
        start = local(p.d, 6.25 if p.run != "long" else 8.0)
        miles = {"easy": [142] * 4, "tempo": [140, 166, 167, 168, 141], "long": [143 + k * 0.6 for k in range(9)]}[p.run]
        if p.run == "easy" and rng.random() < 0.5:
            miles.append(143)
        laps, records, t = [], [], 0.0
        for k, target_hr in enumerate(miles, start=1):
            hr = target_hr + rng.normal(0, 2)
            speed = (0.9 + 0.0155 * hr) * (1 + 0.05 * progress) * (1 + rng.normal(0, 0.015))
            dur = MILE / speed
            laps.append({"lapIndex": k, "distance": MILE, "duration": round(dur, 1),
                         "averageHR": round(hr), "averageRunCadence": round(168 + (speed - 3) * 8)})
            for s in range(0, int(dur), 5):
                drift = min(1.0, (t + s) / 300)  # HR lags at the start
                ts = start + timedelta(seconds=t + s)
                records.append([ts.astimezone(UTC).isoformat(), int(round(hr * (0.8 + 0.2 * drift) + rng.normal(0, 1.5))),
                                round(speed + rng.normal(0, 0.08), 3)])
            t += dur
        dist = MILE * len(miles)
        avg_hr = round(sum(lap["averageHR"] * lap["duration"] for lap in laps) / t)
        rpe = {"easy": 3, "tempo": 7, "long": 5}[p.run] + int(rng.integers(0, 2))
        self.activity_id += int(rng.integers(1000, 9000))
        act_id = self.activity_id
        act = {
            "activityId": act_id, "activityName": f"Maize {'Long ' if p.run == 'long' else ''}Running",
            "activityType": {"typeKey": "running", "typeId": 1, "parentTypeId": 17},
            "startTimeLocal": start.strftime("%Y-%m-%d %H:%M:%S"), "startTimeGMT": gmt_str(start),
            "duration": round(t, 1), "elapsedDuration": round(t + 40, 1), "distance": round(dist, 1),
            "averageHR": avg_hr, "maxHR": int(max(lap["averageHR"] for lap in laps) + 8),
            "averageRunningCadenceInStepsPerMinute": 170, "elevationGain": round(20 + rng.normal(0, 5), 1),
            "calories": int(dist / 1000 * 70), "activityTrainingLoad": round(t / 60 * rpe * 0.35, 1),
        }
        detail = {"activityId": act_id, "summaryDTO": {"duration": round(t, 1)}}
        if rng.random() < 0.8:  # sometimes RPE isn't entered on the watch
            detail["summaryDTO"]["directWorkoutRpe"] = rpe * 10
            detail["summaryDTO"]["directWorkoutFeel"] = int(rng.choice([25, 50, 50, 75]))
        n = store_raw(self.conn, "garmin", "activity", p.d, act)
        n += store_raw(self.conn, "garmin", "activity_detail", p.d, detail)
        n += store_raw(self.conn, "garmin", "activity_splits", p.d, {"activityId": act_id, "lapDTOs": laps})
        fit = {"activityId": act_id, "session": {"sport": "running", "start_time": start.astimezone(UTC).isoformat(),
                                                 "total_timer_time": round(t, 1), "total_distance": round(dist, 1),
                                                 "avg_heart_rate": avg_hr},
               "laps": [], "records": records}
        n += store_raw(c, "garmin_fit", "fit_activity", start.astimezone(UTC).date(), fit)
        sessions.append((start, t / 60, avg_hr, "run"))
        return n

    def _lift(self, p: DayPlan, i: int, sessions: list, garmin: bool) -> int:
        rng, c = self.rng, self.conn
        start = local(p.d, 17.5)
        weeks = i / 7
        injured = self.sprain_day < i < self.sprain_day + 24

        def w(base, rate, cap=None):
            v = base + rate * weeks
            if cap:
                v = min(v, cap)
            return round(v / 2.5) * 2.5

        if p.lift == "lower":
            plan = [("Back squat", 4, 5, w(100, 1.2), 2), ("Romanian deadlift", 3, 8, w(80, 0.8), 2),
                    ("Bulgarian split squat", 3, 8, w(20, 0.3), 2), ("Standing calf raise", 3, 12, w(60, 0.5), 1),
                    ("Copenhagen plank", 2, 6, 0, 2)]
            if i % 14 < 7:
                plan[1] = ("Deadlift", 3, 5, w(130, 1.3), 2)
            if not injured:
                plan.append(("Box jump", 3, 5, 0, 4))
        else:
            plan = [("Bench press", 4, 5, w(80, 1.0, cap=96), 1), ("Barbell row", 4, 8, w(65, 0.6), 2),
                    ("Overhead press", 3, 6, w(50, 0.4), 2), ("Pull-up", 3, 8, 0, 2),
                    ("Dumbbell curl", 2, 12, w(12.5, 0.1), 1), ("Triceps pushdown", 2, 12, w(25, 0.2), 1)]
        rpe = int(rng.integers(6, 8))
        wid = insert(c, "core.workout", {"source": "manual", "sport": "strength", "start_ts": start,
                                         "duration_s": 3600, "rpe": rpe, "rpe_source": "manual",
                                         "notes": f"{p.lift} day"})
        n = 0
        for name, sets, reps, kg, rir in plan:
            ex = self.ex_ids[name]
            if kg >= 40:
                insert(c, "core.lift_set", {"workout_id": wid, "exercise_id": ex, "set_no": 1, "reps": 5,
                                            "weight_kg": round(kg * 0.5 / 2.5) * 2.5, "is_warmup": True},
                       returning=None)
            for s in range(2, sets + 2):
                insert(c, "core.lift_set", {"workout_id": wid, "exercise_id": ex, "set_no": s, "reps": reps,
                                            "weight_kg": kg, "rir": max(0, rir - (1 if s == sets + 1 else 0)),
                                            "is_warmup": False}, returning=None)
                n += 1
        if garmin:  # the watch also recorded this session: exercises the overlap merge
            self.activity_id += int(rng.integers(1000, 9000))
            g_start = start + timedelta(minutes=3)
            store_raw(c, "garmin", "activity", p.d, {
                "activityId": self.activity_id, "activityName": "Strength",
                "activityType": {"typeKey": "strength_training"}, "startTimeLocal": g_start.strftime("%Y-%m-%d %H:%M:%S"),
                "startTimeGMT": gmt_str(g_start), "duration": 3420.0, "averageHR": 112, "maxHR": 151,
                "calories": 310, "activityTrainingLoad": 48.0})
            store_raw(c, "garmin", "activity_detail", p.d, {"activityId": self.activity_id, "summaryDTO": {}})
        sessions.append((start, 60, 112, "lift"))
        return n

    def _wellness(self, p: DayPlan, rhr: float, hrv: float, sessions: list, score: int) -> None:
        rng, c, d = self.rng, self.conn, p.d
        base = local(d, 0)
        values = []
        off = []
        for (start, mins, _, kind) in sessions:
            if kind == "game":
                off.append((start - timedelta(minutes=20), start + timedelta(minutes=mins + 30)))
        charge = base + timedelta(hours=float(rng.uniform(9, 20)))
        off.append((charge, charge + timedelta(minutes=int(rng.integers(25, 55)))))
        if rng.random() < 0.05:
            gap = base + timedelta(hours=float(rng.uniform(8, 14)))
            off.append((gap, gap + timedelta(hours=6)))
        for k in range(720):
            ts = base + timedelta(minutes=2 * k)
            if any(a <= ts < b for a, b in off):
                values.append([ms(ts), None])
                continue
            hour = 2 * k / 60
            hr = rhr + 4 if hour < 6.5 or hour > 23 else 72 + 10 * math.sin(hour / 3)
            for (start, mins, avg, _kind) in sessions:
                if start <= ts < start + timedelta(minutes=mins) and avg:
                    hr = avg
            values.append([ms(ts), int(round(hr + rng.normal(0, 3)))])
        store_raw(c, "garmin", "heart_rates", d, {
            "calendarDate": d.isoformat(), "restingHeartRate": int(round(rhr)),
            "heartRateValueDescriptors": [{"key": "timestamp", "index": 0}, {"key": "heartrate", "index": 1}],
            "heartRateValues": values})
        stress = int(np.clip(30 + rng.normal(0, 6) + (12 if p.sick else 0) + (420 - min(420, score * 5)) / 40, 10, 70))
        bb_high = int(np.clip(45 + hrv * 0.6 + rng.normal(0, 6), 20, 100))
        store_raw(c, "garmin", "daily_summary", d, {
            "calendarDate": d.isoformat(), "totalSteps": int(rng.normal(9500, 2500) + (4000 if p.game else 0)),
            "activeKilocalories": int(rng.normal(700, 150) + (900 if p.game else 0)),
            "totalKilocalories": int(rng.normal(2600, 150) + (900 if p.game else 0)),
            "restingHeartRate": int(round(rhr)), "averageStressLevel": stress,
            "bodyBatteryHighestValue": bb_high, "bodyBatteryLowestValue": int(np.clip(bb_high - 60 - rng.normal(0, 8), 5, 60)),
            "moderateIntensityMinutes": int(rng.integers(10, 50)), "vigorousIntensityMinutes": int(rng.integers(0, 40))})
        if any(kind == "run" for *_, kind in sessions):
            store_raw(c, "garmin", "max_metrics", d, [{"generic": {
                "calendarDate": d.isoformat(), "vo2MaxPreciseValue": round(49.5 + 2.5 * (d - self.start).days / self.days
                                                                           + rng.normal(0, 0.2), 1)}}])

    def _food(self, p: DayPlan, weight: float) -> int:
        rng, c, d = self.rng, self.conn, p.d
        complete = rng.random() < 0.86 and not p.travel
        entries: list[tuple[float, str, str, float]] = []  # hour, meal, food, servings
        entries.append((7.2, "breakfast", "Rolled oats", 2))
        entries.append((7.2, "breakfast", "Banana", 1))
        entries.append((7.3, "breakfast", "Coffee, brewed", 1))
        entries.append((7.2, "breakfast", rng.choice(["Greek yogurt, plain nonfat", "Eggs", "Whey protein"]), 1))
        if rng.random() < 0.6:
            entries.append((7.2, "breakfast", "Whole wheat toast", 1))
            entries.append((7.2, "breakfast", "Peanut butter", 1))
        lunch = rng.choice(["Turkey sandwich", "Burrito bowl", "Chicken breast, cooked", "Spinach salad"])
        entries.append((12.4, "lunch", lunch, 1.5 if lunch == "Turkey sandwich" else 1))
        entries.append((12.4, "lunch", rng.choice(["Apple", "Banana", "Black beans"]), 1))
        if lunch in ("Chicken breast, cooked", "Spinach salad"):
            entries.append((12.4, "lunch", "White rice, cooked", 1))
            entries.append((12.4, "lunch", "Chicken breast, cooked" if lunch == "Spinach salad" else "Broccoli", 1))
        if rng.random() < 0.7:
            entries.append((15.5, "snack", rng.choice(["Apple", "Almonds", "Protein bar", "Trail mix"]), 1))
        if p.caffeine_late:
            entries.append((p.caffeine_late, "snack", rng.choice(["Cold brew", "Energy drink", "Coffee, brewed"]), 1))
        dinner_hour = 21.4 if p.late_meal else 18.6 + rng.normal(0, 0.3)
        if p.game and p.game[0][0] >= 19:
            entries.append((17.5, "pre_workout", "Bagel with cream cheese", 1))
            entries.append((21.0, "post_workout", "Chocolate milk", 1))
        dinners = [("Salmon, cooked", "Sweet potato"), ("Ground beef 90/10, cooked", "Tortillas, flour"),
                   ("Pasta, cooked", "Marinara sauce"), ("Chicken breast, cooked", "White rice, cooked"),
                   ("Pizza", None), ("Black beans", "Tortillas, flour")]
        dinner = dinners[int(rng.integers(len(dinners)))]
        entries.append((dinner_hour, "dinner", dinner[0], 1.5))
        if dinner[1]:
            entries.append((dinner_hour, "dinner", dinner[1], 1.5))
        entries.append((dinner_hour, "dinner", "Spinach salad", 1) if rng.random() < 0.35 else
                       (dinner_hour, "dinner", "Milk, 2%", 1))
        if rng.random() < 0.5:
            entries.append((dinner_hour, "dinner", "Broccoli", 1))
        if rng.random() < 0.45:
            entries.append((20.5, "snack", rng.choice(["Greek yogurt, plain nonfat", "Peanut butter", "Milk, 2%"]), 1))
        for k in range(p.drinks):
            entries.append((20.0 + 0.6 * k, "snack", rng.choice(["Beer, IPA", "Beer, light"]), 1))
        if not complete:  # partial logging: dinner and later never got entered
            entries = [e for e in entries if e[0] < 15]
        n = 0
        for hour, meal, food, servings in entries:
            insert(c, "core.food_entry", {"logged_ts": local(d, hour + rng.normal(0, 0.05)), "date": d,
                                          "meal": meal, "catalog_id": self.food_ids[food],
                                          "servings": servings}, returning=None)
            n += 1
        upsert(c, "core.food_day_status", [{"date": d, "complete": complete}], conflict=["date"])
        return n


def _clip5(v: float) -> int:
    return int(np.clip(round(v), 1, 5))


DEMO_ACTIVE_RULES = ("NUTR_PROTEIN_LOW", "NUTR_CALORIE_DRIFT", "TRAIN_POST_GAME", "SLEEP_SHORT",
                     "RECOV_HIGH_LOAD_LOW_HRV", "RECOV_ILLNESS_FLAG", "TRAIN_PRE_GAME_LEGS")


def generate(conn: psycopg.Connection, days: int = 180, end: date | None = None, seed: int = 42) -> dict:
    gen = Generator(conn, days=days, end=end, seed=seed)
    counts = gen.run()
    # Phase-2 style: the fixed-target starter rules are live in the demo.
    execute(conn, "UPDATE analytics.rule SET status = 'active' WHERE rule_id = ANY(%s)", (list(DEMO_ACTIVE_RULES),))
    # Start the caffeine cutoff at a guess (16:00): the weekly job should find 14:00 in the data.
    execute(conn, """UPDATE analytics.rule_parameter SET value = 16
                     WHERE rule_id = 'SLEEP_CAFFEINE_CUTOFF' AND name = 'cutoff_hour'""")
    return {"start": gen.start, "end": gen.end, **counts}
