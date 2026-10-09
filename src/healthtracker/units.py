"""Unit conversion. Storage is always metric; display follows profile.display_units."""

from __future__ import annotations

KG_PER_LB = 0.45359237
M_PER_MI = 1609.344


def kg_to_lb(kg: float | None) -> float | None:
    return None if kg is None else kg / KG_PER_LB


def lb_to_kg(lb: float | None) -> float | None:
    return None if lb is None else lb * KG_PER_LB


def km_to_mi(km: float | None) -> float | None:
    return None if km is None else km * 1000 / M_PER_MI


def pace_s_per_km_to_s_per_mi(s_km: float | None) -> float | None:
    return None if s_km is None else s_km * M_PER_MI / 1000


def fmt_pace(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def fmt_hour(hour: float | None) -> str | None:
    """Decimal local hour (may exceed 24 for after-midnight) -> 'HH:MM'."""
    if hour is None:
        return None
    total = round(hour * 60) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


class Display:
    """Converts metric values for the user's chosen display units."""

    def __init__(self, units: str = "imperial"):
        self.imperial = units == "imperial"

    @property
    def weight_unit(self) -> str:
        return "lb" if self.imperial else "kg"

    @property
    def distance_unit(self) -> str:
        return "mi" if self.imperial else "km"

    @property
    def pace_unit(self) -> str:
        return "/mi" if self.imperial else "/km"

    def weight(self, kg: float | None, nd: int = 1) -> float | None:
        if kg is None:
            return None
        return round(kg_to_lb(kg) if self.imperial else kg, nd)

    def distance_km(self, km: float | None, nd: int = 1) -> float | None:
        if km is None:
            return None
        return round(km_to_mi(km) if self.imperial else km, nd)

    def pace(self, s_per_km: float | None) -> str | None:
        if s_per_km is None:
            return None
        s = pace_s_per_km_to_s_per_mi(s_per_km) if self.imperial else s_per_km
        return f"{fmt_pace(s)}{self.pace_unit}"
