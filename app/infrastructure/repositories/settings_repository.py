import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.settings import UserSettings
from app.infrastructure.repositories.base_repository import BaseRepository


class SettingsRepository(BaseRepository[UserSettings]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, UserSettings)

    def get_for_user(self, user_id: uuid.UUID) -> UserSettings | None:
        stmt = select(UserSettings).where(UserSettings.user_id == user_id)
        return self.db.execute(stmt).scalar_one_or_none()
