import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID
from app.infrastructure.models.enums import BrokerAccountStatus


class BrokerAccount(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "broker_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    broker_name: Mapped[str] = mapped_column(String(64), nullable=False)
    account_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_type: Mapped[str] = mapped_column(String(32), default="demo", nullable=False)

    # Security: credentials are NEVER stored in plaintext (07/12/14 docs).
    # Sprint 1 does not implement real broker connectivity (10_Live_Trading_Engine.md
    # is out of scope this sprint), so these columns exist for schema completeness
    # but are only ever populated by a future encryption-at-rest service — never
    # logged, never returned by the API in plaintext.
    encrypted_api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    encrypted_secret: Mapped[str | None] = mapped_column(String(512), nullable=True)
    encrypted_password: Mapped[str | None] = mapped_column(String(512), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), default=BrokerAccountStatus.DISCONNECTED.value, nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    live_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    """Sprint 4 addition. The explicit 'mode switching requires user
    confirmation' gate from 10_Live_Trading_Engine.md's Trading Modes
    section: connecting a broker (status=CONNECTED) is NOT the same as
    authorizing it to place real trades. Only a dedicated confirmation
    endpoint may set this True; LiveExecutionEngine refuses to place any
    order when it's False, regardless of connection status."""
    last_health_check_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_connection_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Diagnostics for Heartbeat Monitoring / Circuit Breakers (Sprint 4
    modules 8-9) — the last observed failure, kept for operator visibility
    without needing to dig through logs."""

    user: Mapped["User"] = relationship(back_populates="broker_accounts")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BrokerAccount {self.broker_name} ({self.status})>"
