"""Runtime settings, read from environment variables or a .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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

    # Local LLM (Ollama). The LLM only phrases decisions; disable to always use the template.
    llm_enabled: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3:14b"
    llm_timeout_s: float = 180.0
    prompt_version: str = "v1"

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
