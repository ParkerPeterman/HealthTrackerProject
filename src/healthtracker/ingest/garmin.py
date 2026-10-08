"""Pull Garmin Connect data into raw.ingest.

Uses the unofficial `garminconnect` library. Payloads are stored exactly as
returned so the parsers in transform/garmin.py can be fixed and re-run without
re-fetching. If the API breaks, FIT files exported from Garmin Connect can be
dropped into data/inbox and loaded with `healthtracker import-fit`.
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from collections.abc import Callable
from datetime import date, timedelta
from getpass import getpass
from typing import Any

from healthtracker.config import get_settings
from healthtracker.db import connect
from healthtracker.ingest.fit import parse_fit_bytes
from healthtracker.ingest.raw import store_raw

log = logging.getLogger("healthtracker.garmin")

DAILY_ENDPOINTS: dict[str, str] = {
    "sleep": "get_sleep_data",
    "hrv": "get_hrv_data",
    "daily_summary": "get_user_summary",
    "heart_rates": "get_heart_rates",
    "max_metrics": "get_max_metrics",
}

RUN_TYPES = {"running", "treadmill_running", "trail_running", "track_running", "street_running"}


class GarminNotLoggedIn(RuntimeError):
    pass


def login_interactive(email: str | None = None) -> None:
    """One-time login. Prompts for password and MFA; saves tokens, never the password."""
    from garminconnect import Garmin

    s = get_settings()
    email = email or s.garmin_email or input("Garmin email: ")
    tokenstore = s.garmin_tokenstore.expanduser()
    tokenstore.mkdir(parents=True, exist_ok=True)
    client = Garmin(email, getpass("Garmin password: "), prompt_mfa=lambda: input("MFA code: "))
    client.login(str(tokenstore))
    log.info("Garmin tokens saved to %s", tokenstore)


def client_from_tokens():
    from garminconnect import Garmin, GarminConnectAuthenticationError

    tokenstore = get_settings().garmin_tokenstore.expanduser()
    if not (tokenstore / "garmin_tokens.json").exists():
        raise GarminNotLoggedIn(
            f"No Garmin tokens in {tokenstore}. Run `healthtracker garmin-login` once."
        )
    client = Garmin()
    try:
        client.login(str(tokenstore))
    except GarminConnectAuthenticationError as exc:
        raise GarminNotLoggedIn(f"Stored Garmin tokens were rejected: {exc}") from exc
    return client


def _call(fn: Callable[..., Any], *args: Any, retries: int = 3) -> Any:
    from garminconnect import GarminConnectNotFoundError, GarminConnectTooManyRequestsError

    for attempt in range(retries):
        try:
            return fn(*args)
        except GarminConnectNotFoundError:
            return None
        except GarminConnectTooManyRequestsError:
            wait = 30 * (attempt + 1)
            log.warning("Garmin rate limit; waiting %ss", wait)
            time.sleep(wait)
    return fn(*args)


def fetch_days(client, start: date, end: date, pause_s: float = 0.3) -> int:
    """Fetch the daily endpoints for each date in [start, end]. Returns new raw rows."""
    new = 0
    day = start
    while day <= end:
        with connect() as conn:
            for endpoint, method in DAILY_ENDPOINTS.items():
                payload = _call(getattr(client, method), day.isoformat())
                if payload in (None, {}, []):
                    continue
                new += store_raw(conn, "garmin", endpoint, day, payload)
                time.sleep(pause_s)
        day += timedelta(days=1)
    return new


def fetch_activities(client, start: date, end: date, pause_s: float = 0.3) -> int:
    """Fetch activities in [start, end] plus detail, splits, and (runs) FIT samples."""
    s = get_settings()
    activities = _call(client.get_activities_by_date, start.isoformat(), end.isoformat()) or []
    new = 0
    for act in activities:
        act_id = act.get("activityId")
        local_start = (act.get("startTimeLocal") or act.get("startTimeGMT") or "")[:10]
        for_date = date.fromisoformat(local_start) if local_start else None
        with connect() as conn:
            new += store_raw(conn, "garmin", "activity", for_date, act)
            detail = _call(client.get_activity, str(act_id))
            if detail:
                new += store_raw(conn, "garmin", "activity_detail", for_date, detail)
            type_key = (act.get("activityType") or {}).get("typeKey", "")
            if type_key in RUN_TYPES:
                splits = _call(client.get_activity_splits, str(act_id))
                if splits:
                    new += store_raw(conn, "garmin", "activity_splits", for_date, splits)
                if s.garmin_fetch_fit:
                    fit = _download_fit(client, act_id)
                    if fit:
                        fit["activityId"] = act_id
                        new += store_raw(conn, "garmin_fit", "fit_activity", for_date, fit)
        time.sleep(pause_s)
    return new


def _download_fit(client, activity_id: int) -> dict | None:
    from garminconnect import Garmin

    try:
        blob = _call(client.download_activity, str(activity_id), Garmin.ActivityDownloadFormat.ORIGINAL)
    except Exception:  # noqa: BLE001 - FIT is optional enrichment
        log.warning("FIT download failed for %s", activity_id, exc_info=True)
        return None
    if not blob:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith(".fit")), None)
            if not name:
                return None
            return parse_fit_bytes(zf.read(name))
    except zipfile.BadZipFile:
        return parse_fit_bytes(blob)


def pull(start: date, end: date) -> int:
    client = client_from_tokens()
    return fetch_days(client, start, end) + fetch_activities(client, start, end)
