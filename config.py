"""Application configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env if present (safe to skip in production environments)
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    exchange_api_key: str
    refresh_minutes: int
    base_currency: str
    rates_file: Path
    meta_file: Path

    @property
    def has_token(self) -> bool:
        return bool(self.telegram_token) and self.telegram_token != "your_telegram_bot_token_here"


def load_settings() -> Settings:
    return Settings(
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        exchange_api_key=os.getenv("EXCHANGE_API_KEY", "").strip(),
        refresh_minutes=max(1, _int_env("RATES_REFRESH_MINUTES", 60)),
        base_currency=os.getenv("BASE_CURRENCY", "USD").strip().upper(),
        rates_file=DATA_DIR / "rates.json",
        meta_file=DATA_DIR / "rates_meta.json",
    )


SETTINGS = load_settings()
