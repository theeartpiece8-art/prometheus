"""
Centralized application configuration.

All configuration is loaded from environment variables (see .env.example).
No secrets or environment-specific values may be hardcoded elsewhere in
the codebase — this is the single source of truth, per
12_Coding_Standards.md ("Use environment variables for secrets",
"No hardcoded values").
"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- General ---
    APP_NAME: str = "PROMETHEUS Quant Lab"
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    # --- Security / JWT ---
    SECRET_KEY: str = Field(
        default="CHANGE_ME_INSECURE_DEV_ONLY_SECRET_KEY_MIN_32_CHARS",
        description="HMAC signing key for JWTs. MUST be overridden in staging/production.",
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # --- Database ---
    DATABASE_URL: str = "postgresql+psycopg2://prometheus:prometheus@localhost:5432/prometheus"

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = True

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # --- Risk Engine defaults (used to seed a new user's risk settings) ---
    DEFAULT_RISK_PER_TRADE_PCT: float = 1.0
    DEFAULT_MAX_DAILY_LOSS_PCT: float = 3.0
    DEFAULT_MAX_DRAWDOWN_PCT: float = 10.0
    DEFAULT_MAX_OPEN_POSITIONS: int = 10
    DEFAULT_MAX_POSITIONS_PER_SYMBOL: int = 2
    DEFAULT_MAX_PORTFOLIO_EXPOSURE_PCT: float = 50.0
    DEFAULT_MAX_SYMBOL_EXPOSURE_PCT: float = 20.0
    DEFAULT_MAX_LEVERAGE: float = 10.0
    DEFAULT_STARTING_BALANCE: float = 10000.0

    # --- Market data ---
    MARKET_DATA_ALLOW_LIVE_FETCH: bool = True
    """
    If True, the market data service will attempt to fetch real historical
    data via yfinance. If the fetch fails for any reason (no network access,
    invalid symbol, rate limiting, etc.) it transparently falls back to
    deterministic synthetic OHLCV data, and the response is labeled
    accordingly (`data_source: "mock"` vs `"yfinance"`) so callers are never
    misled about whether they're looking at real or synthetic data.
    """

    # --- Logging ---
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    @field_validator("SECRET_KEY")
    @classmethod
    def _warn_on_insecure_secret(cls, v: str) -> str:
        # Deliberately not raising here: we want `docker compose up` to work
        # out of the box in development. Production readiness is enforced
        # at deploy time (see README "Production checklist").
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_testing(self) -> bool:
        return self.ENVIRONMENT == "test"


@lru_cache
def get_settings() -> Settings:
    """Settings are cached — env vars are read once per process."""
    return Settings()
