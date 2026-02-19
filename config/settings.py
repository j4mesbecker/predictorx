"""
PredictorX — Unified Configuration
Single source of truth for all credentials and settings.
"""

from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformSettings(BaseSettings):
    """Platform settings loaded from .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Telegram (PredictorX Bot) ─────────────────────────
    telegram_bot_token: str = Field("", description="Telegram bot token")
    telegram_chat_id: str = Field("", description="Telegram chat ID")
    friday_bot_token: str = Field("", description="FRIDAY bot token for routing alerts through FRIDAY")

    # ── Kalshi API ────────────────────────────────────────
    kalshi_api_key_id: str = Field("", description="Kalshi API key ID")
    kalshi_private_key_path: str = Field("./kalshi_key.pem", description="Path to Kalshi RSA private key")
    kalshi_env: str = Field("production", description="Kalshi environment")

    # ── Weather APIs ──────────────────────────────────────
    nws_user_agent: str = Field("PredictorX/1.0", description="NWS API user agent")
    weatherapi_key: Optional[str] = Field(None, description="WeatherAPI.com key")
    visualcrossing_key: Optional[str] = Field(None, description="VisualCrossing key")

    # ── Anthropic (optional) ──────────────────────────────
    anthropic_api_key: Optional[str] = Field(None, description="Anthropic API key for Claude analysis")

    # ── Database ──────────────────────────────────────────
    database_path: str = Field("./data/predictions.db", description="SQLite database path")

    # ── Web Dashboard ─────────────────────────────────────
    web_host: str = Field("127.0.0.1", description="Web server host")
    web_port: int = Field(8000, description="Web server port")

    # ── Risk Management ───────────────────────────────────
    starting_capital: float = Field(500.0, description="Starting capital")
    daily_risk_pct: float = Field(0.05, description="Daily risk as fraction of capital")

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def kalshi_configured(self) -> bool:
        return bool(self.kalshi_api_key_id and Path(self.kalshi_private_key_path).exists())

    @property
    def database_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path}"

    @property
    def database_sync_url(self) -> str:
        return f"sqlite:///{self.database_path}"

    def ensure_dirs(self) -> None:
        """Create required directories."""
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        Path("./data/forecast_cache").mkdir(parents=True, exist_ok=True)
        Path("./data/logs").mkdir(parents=True, exist_ok=True)


# Global singleton
_settings: Optional[PlatformSettings] = None


def get_settings() -> PlatformSettings:
    global _settings
    if _settings is None:
        _settings = PlatformSettings()
        _settings.ensure_dirs()
    return _settings
