import datetime as dt

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, UUIDPrimaryKeyMixin


class SystemLog(UUIDPrimaryKeyMixin, Base):
    """
    Persisted mirror of selected structured JSON log events (see
    app/infrastructure/logging/logger.py for the primary stdout logging
    pipeline). Only significant events are persisted here to avoid
    turning this table into an unbounded firehose; routine request logs
    stay in stdout/log aggregation only.
    """
    __tablename__ = "system_logs"

    module: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    execution_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
