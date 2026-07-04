import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID
from app.infrastructure.models.enums import RiskEventSeverity


class RiskEvent(UUIDPrimaryKeyMixin, Base):
    """
    Immutable audit record of risk decisions, per 07_Risk_Management_Engine.md
    ("Audit Logging" — "Logs must be immutable") and 04_Database_Design.md
    ("Maintain immutable audit logs for critical events").

    Application code must only ever INSERT rows here — never UPDATE or DELETE.
    """
    __tablename__ = "risk_events"

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default=RiskEventSeverity.MEDIUM.value, nullable=False)
    action_taken: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="risk_events")
