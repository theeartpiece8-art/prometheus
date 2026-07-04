import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID


class Backtest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "backtests"

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    initial_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    ending_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    win_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    expectancy: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    profit_factor: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    sharpe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    sortino_ratio: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    """Sprint 2 addition alongside sharpe_ratio — same rationale: a headline
    risk-adjusted-return metric worth its own queryable column (e.g. 'find
    backtests with sortino > 2'), distinct from the detailed trade-by-trade
    data that lives in `results` JSON."""
    max_drawdown: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    report_location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    """
    Sprint 2 status lifecycle: 'running' (set immediately on insert, before
    the synchronous simulation executes) -> 'completed' | 'failed'.
    ('queued' remains the column default for schema-level safety but the
    application no longer leaves a row in that state — Sprint 1's fire-and-
    forget stub is gone; see backtest_service.py.)
    """

    # --- Sprint 2 additions ---
    # 04_Database_Design.md's BACKTESTS table has no `symbol` or `timeframe`
    # column, but Sprint 2 explicitly requires the engine to take
    # "strategy + symbol + timeframe" as input — both need to be persisted
    # to make a completed run reproducible/auditable. Added here rather than
    # overloading the existing `dataset` field, which means something
    # different (a named historical dataset source, not necessarily a ticker).
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    timeframe: Mapped[str | None] = mapped_column(String(16), nullable=True)

    results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    """
    Structured output not suited to individual relational columns: the full
    simulated trade log, the equity curve, and the risk-rejection log —
    the same flexible-JSON pattern already used for Strategy.parameters and
    UserSettings.risk_settings. Shape: {"trades": [...], "equity_curve":
    [...], "risk_rejections": [...], "data_source": "yfinance"|"mock",
    "bars_processed": int, "commission_pct": str}.
    """

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Populated when status == 'failed' — e.g. no historical data available,
    unknown strategy type, or a date range producing zero usable bars."""

    strategy: Mapped["Strategy"] = relationship(back_populates="backtests")

