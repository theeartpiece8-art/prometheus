import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID


class Portfolio(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "portfolios"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), default="Default Portfolio", nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("10000"), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("10000"), nullable=False)
    margin_used: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    free_margin: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("10000"), nullable=False)
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)

    # Extension beyond the base schema: the Risk Management Engine spec
    # (07_Risk_Management_Engine.md) requires a Kill Switch that halts ALL
    # new orders for an account. It has to live somewhere queryable per-
    # portfolio; this is that field. Defaults to inactive.
    kill_switch_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    peak_equity: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("10000"), nullable=False)
    """Tracks the historical high-water mark of equity, used for drawdown %."""

    user: Mapped["User"] = relationship(back_populates="portfolios")
    positions: Mapped[list["Position"]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")
    orders: Mapped[list["Order"]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")
    equity_history: Mapped[list["EquityHistory"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )
    risk_events: Mapped[list["RiskEvent"]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Portfolio {self.name} equity={self.equity}>"
