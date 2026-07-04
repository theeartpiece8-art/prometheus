from decimal import Decimal

from pydantic import BaseModel

from app.application.schemas.common import ORMModel


class SettingsUpdateRequest(BaseModel):
    theme: str | None = None
    language: str | None = None
    timezone: str | None = None
    default_risk: Decimal | None = None
    notification_preferences: dict | None = None


class SettingsResponse(ORMModel):
    theme: str
    language: str
    timezone: str
    default_risk: Decimal
    notification_preferences: dict
    risk_settings: dict
