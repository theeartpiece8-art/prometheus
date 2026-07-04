import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID


class Trade(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "trades"

    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("positions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    gross_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    commission: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    spread_cost: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    slippage_cost: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=Decimal("0"), nullable=False)
    trade_duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # seconds
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    strategy: Mapped["Strategy | None"] = relationship(back_populates="trades")
    order: Mapped["Order"] = relationship(back_populates="trades")
    position: Mapped["Position | None"] = relationship(back_populates="trades")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Trade {self.symbol} net_profit={self.net_profit}>"
