"""Runtime settings, read from environment variables or a .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://health:health@127.0.0.1:5432/healthtracker"
    demo_database_url: str = "postgresql://health:health@127.0.0.1:5432/healthtracker_demo"
    test_database_url: str | None = None
    migrations_dir: Path = REPO_ROOT / "db" / "migrations"

    # Local zone used until the profile row says otherwise.
    tz: str = "America/Chicago"

    # Garmin Connect (unofficial API). Password is never stored: `healthtracker garmin-login`
    # prompts for it once and saves refresh tokens to the tokenstore.
    garmin_email: str | None = None
    garmin_tokenstore: Path = Path("~/.garminconnect").expanduser()
    garmin_fetch_fit: bool = True        # download FIT for runs to get per-second HR
    garmin_lookback_days: int = 3        # nightly re-fetch window (late syncs, edits)

    # LLM. It only phrases decisions; disable to always use the template.
    # Provider "ollama" talks to OLLAMA_URL's /api/chat; "openai" talks to any OpenAI-compatible
    # server at LLM_BASE_URL (…/v1) via /chat/completions. The same payload goes to either.
    llm_enabled: bool = True
    llm_provider: Literal["ollama", "openai"] = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    llm_base_url: str | None = None
    llm_api_key: str = "none"
    llm_model: str = "qwen3:14b"
    llm_timeout_s: float = 120.0
    prompt_version: str = "v2"  # v2: no medication, painkillers, or extra water (H4)

    # Weather (Open-Meteo, A7). Home location is rounded to 0.1 degree (about 11 km) before any
    # request; only the rounded point and dates are ever sent.
    home_lat: float | None = None
    home_lon: float | None = None
    weather_enabled: bool = True

    # Data quality
    partial_day_wear_min: int = 1200     # under 20h of all-day HR = partial day
    feature_rebuild_days: int = 14       # nightly rebuild window for daily_features

    # Services
    bind_host: str = "127.0.0.1"
    api_port: int = 8000
    dashboard_port: int = 8501
    web_dist: Path = REPO_ROOT / "web" / "dist"


@lru_cache
def get_settings() -> Settings:
    return Settings()
