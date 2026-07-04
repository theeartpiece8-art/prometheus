import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.notification import Notification
from app.infrastructure.repositories.base_repository import BaseRepository


class NotificationRepository(BaseRepository[Notification]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Notification)

    def list_for_user(self, user_id: uuid.UUID, *, offset: int = 0, limit: int = 100) -> list[Notification]:
        stmt = (
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_for_user(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> Notification | None:
        stmt = select(Notification).where(Notification.id == notification_id, Notification.user_id == user_id)
        return self.db.execute(stmt).scalar_one_or_none()
