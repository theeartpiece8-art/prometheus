from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.infrastructure.models.notification import Notification
from app.infrastructure.repositories.notification_repository import NotificationRepository


class NotificationNotFoundError(Exception):
    pass


class NotificationService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = NotificationRepository(db)

    def list_for_user(self, user_id: uuid.UUID, offset: int = 0, limit: int = 100) -> list[Notification]:
        return self.repo.list_for_user(user_id, offset=offset, limit=limit)

    def mark_read(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> Notification:
        notification = self.repo.get_for_user(notification_id, user_id)
        if notification is None:
            raise NotificationNotFoundError("Notification not found.")
        notification.is_read = True
        self.db.commit()
        self.db.refresh(notification)
        return notification
