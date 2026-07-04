import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID


class EquityHistory(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "equity_history"

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    balance: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    drawdown: Mapped[Decimal] = mapped_column(Numeric(6, 3), default=Decimal("0"), nullable=False)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="equity_history")
