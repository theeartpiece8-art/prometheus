import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
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

    user: Mapped["User"] = relationship(back_populates="broker_accounts")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BrokerAccount {self.broker_name} ({self.status})>"
