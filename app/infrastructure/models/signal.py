import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID


class Signal(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "signals"

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(16), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(16), nullable=False)  # buy / sell / hold
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    strategy: Mapped["Strategy"] = relationship(back_populates="signals")
