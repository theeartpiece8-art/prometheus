import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID
from app.infrastructure.models.enums import PositionStatus


class Position(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "positions"

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    average_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    """
    Sprint 3 addition: 04_Database_Design.md's POSITIONS table has no SL/TP
    columns -- Sprint 1 only needed the *originating order's* stop_loss/
    take_profit for a one-time simulated fill. Continuous monitoring (Paper
    Trading Engine, 09_Paper_Trading_Engine.md's "Position Management: Stop
    Loss, Take Profit... Trailing Stop") needs to know a position's CURRENT
    protective levels independent of which order opened it -- e.g. a
    position built from multiple fills, or a future trailing-stop update,
    can have levels that diverge from the original entry order. Set on
    open/add in OrderService, checked every tick in PaperTradingService.
    """
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=PositionStatus.OPEN.value, nullable=False, index=True)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="positions")
    trades: Mapped[list["Trade"]] = relationship(back_populates="position")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Position {self.direction} {self.quantity} {self.symbol} ({self.status})>"
