import uuid
from decimal import Decimal

from sqlalchemy import JSON, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.infrastructure.database.base import Base, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID

_cfg = get_settings()


def _default_risk_settings() -> dict:
    """
    Seed values for the structured risk configuration block.

    Reconciliation note: 04_Database_Design.md's SETTINGS table only lists a
    single `default_risk` field, but 07_Risk_Management_Engine.md requires a
    much richer set of configurable limits (risk per trade, daily/weekly/
    monthly loss, drawdown, exposure, leverage, allowed symbols, etc.) and
    states risk management has final authority over the system. Where the two
    documents conflict, this implementation follows the Risk Management
    Engine doc and stores the full structured config in `risk_settings`
    (JSON), while keeping `default_risk` for simple display/back-compat.
    """
    return {
        "risk_per_trade_pct": _cfg.DEFAULT_RISK_PER_TRADE_PCT,
        "max_daily_loss_pct": _cfg.DEFAULT_MAX_DAILY_LOSS_PCT,
        "max_weekly_loss_pct": _cfg.DEFAULT_MAX_DAILY_LOSS_PCT * 3,
        "max_monthly_loss_pct": _cfg.DEFAULT_MAX_DAILY_LOSS_PCT * 8,
        "max_drawdown_pct": _cfg.DEFAULT_MAX_DRAWDOWN_PCT,
        "max_open_positions": _cfg.DEFAULT_MAX_OPEN_POSITIONS,
        "max_positions_per_symbol": _cfg.DEFAULT_MAX_POSITIONS_PER_SYMBOL,
        "max_portfolio_exposure_pct": _cfg.DEFAULT_MAX_PORTFOLIO_EXPOSURE_PCT,
        "max_symbol_exposure_pct": _cfg.DEFAULT_MAX_SYMBOL_EXPOSURE_PCT,
        "max_leverage": _cfg.DEFAULT_MAX_LEVERAGE,
        "max_spread": None,
        "max_slippage": None,
        "min_account_balance": 0,
        "allowed_symbols": None,
        "allowed_trading_sessions": None,
    }


class UserSettings(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    theme: Mapped[str] = mapped_column(String(16), default="dark", nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    default_risk: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal(str(_cfg.DEFAULT_RISK_PER_TRADE_PCT)), nullable=False
    )
    notification_preferences: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    risk_settings: Mapped[dict] = mapped_column(JSON, default=_default_risk_settings, nullable=False)

    user: Mapped["User"] = relationship(back_populates="settings")
