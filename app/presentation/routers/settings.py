from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.application.schemas.settings import SettingsResponse, SettingsUpdateRequest
from app.application.services.settings_service import SettingsService
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.user import User

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("", response_model=SettingsResponse)
def get_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return SettingsService(db).get(current_user.id)


@router.put("", response_model=SettingsResponse)
def update_settings(
    payload: SettingsUpdateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    return SettingsService(db).update(current_user.id, **payload.model_dump(exclude_unset=True))
