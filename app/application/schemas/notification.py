import datetime as dt
import uuid

from app.application.schemas.common import ORMModel


class NotificationResponse(ORMModel):
    id: uuid.UUID
    type: str
    title: str
    message: str
    severity: str
    is_read: bool
    created_at: dt.datetime
