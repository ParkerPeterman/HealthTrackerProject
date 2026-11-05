"""Medication adherence (workstream H): a daily check, adherence features, approved messages.

Rules that hold everywhere in this package:
  * an unknown day (no answer, or "don't remember") is never counted or shown as missed
  * the system never advises on medication: no dose, timing, skipping, or stopping advice
  * medication is never an experiment, never a lever, and never ranked as something to change
  * every message comes from an approved template (messages.py) filled with code-computed values,
    and is added after the LLM output guard: the LLM prompt never contains medication data
  * the tables are sensitive: kept out of daily_features and the recommendation snapshot, shown
    on the dashboard only behind an opt-in toggle, and never in demo screenshots
"""

SENSITIVE_TABLES = ("core.medication", "core.medication_change", "core.medication_log",
                    "analytics.medication_daily", "analytics.medication_message")
# Names that mark a feature or exposure as medication-derived (never an experiment or a lever).
SENSITIVE_PREFIXES = ("med_", "medication", "dose_", "adherence_", "consecutive_missed", "days_since_dose_change")


def is_sensitive(name: str) -> bool:
    return name.strip().lower().startswith(SENSITIVE_PREFIXES)
