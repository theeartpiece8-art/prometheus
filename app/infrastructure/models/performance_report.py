import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base, UUIDPrimaryKeyMixin
from app.infrastructure.database.types import GUID


class PerformanceReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "performance_reports"

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_name: Mapped[str] = mapped_column(String(255), nullable=False)
    report_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    generated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    strategy: Mapped["Strategy"] = relationship()
