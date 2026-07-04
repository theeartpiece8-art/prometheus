import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID


class PaperTradingSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Sprint 3 addition — not part of 04_Database_Design.md's original schema,
    since Sprint 1 had no concept of an automated, continuously-running
    trading session (only one-off manual orders). Required by
    09_Paper_Trading_Engine.md's "Session Management" section (Start/Pause/
    Resume/Stop/Reset) and "Validation Rules" (reject startup if invalid).

    Deliberately runs against the user's EXISTING default portfolio rather
    than a separate isolated "paper account" — see README Sprint 3 section
    for the full reasoning. This means the Risk Engine's portfolio-wide
    exposure/drawdown checks automatically coordinate across multiple
    concurrently-running strategies with no extra isolation logic needed.
    """
    __tablename__ = "paper_trading_sessions"

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="created", nullable=False, index=True)
    """created | running | paused | stopped | interrupted"""

    tick_interval_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Populated on validation failure at start, or when auto-marked
    'interrupted' at app startup after an unclean shutdown."""

    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_tick_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tick_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    items: Mapped[list["PaperTradingSessionItem"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PaperTradingSession {self.id} ({self.status})>"


class PaperTradingSessionItem(UUIDPrimaryKeyMixin, Base):
    """One (strategy, symbol, timeframe) combination tracked by a session —
    supports 09_Paper_Trading_Engine.md's "Multiple strategy paper trading"
    and "Multi-asset portfolio" operating modes: a session can track many
    of these simultaneously."""
    __tablename__ = "paper_trading_session_items"

    session_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("paper_trading_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(16), default="1D", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped["PaperTradingSession"] = relationship(back_populates="items")
    strategy: Mapped["Strategy"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PaperTradingSessionItem {self.symbol} strategy={self.strategy_id}>"
