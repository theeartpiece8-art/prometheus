from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.infrastructure.models.settings import UserSettings
from app.infrastructure.repositories.settings_repository import SettingsRepository


class SettingsNotFoundError(Exception):
    pass


class SettingsService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = SettingsRepository(db)

    def get(self, user_id: uuid.UUID) -> UserSettings:
        settings = self.repo.get_for_user(user_id)
        if settings is None:
            raise SettingsNotFoundError(
                "No settings row exists for this user. This should not happen — "
                "settings are created automatically at registration."
            )
        return settings

    def update(self, user_id: uuid.UUID, **changes) -> UserSettings:
        settings = self.get(user_id)
        for field, value in changes.items():
            if value is not None:
                setattr(settings, field, value)
        self.db.commit()
        self.db.refresh(settings)
        return settings

    def update_risk_settings(self, user_id: uuid.UUID, risk_changes: dict) -> UserSettings:
        from decimal import Decimal

        settings = self.get(user_id)
        current = dict(settings.risk_settings or {})
        for key, value in risk_changes.items():
            if value is None:
                continue
            if isinstance(value, Decimal):
                current[key] = float(value)
            else:
                current[key] = value
        settings.risk_settings = current
        self.db.commit()
        self.db.refresh(settings)
        return settings
