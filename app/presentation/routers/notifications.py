import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.application.schemas.notification import NotificationResponse
from app.application.services.notification_service import NotificationNotFoundError, NotificationService
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.user import User

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=list[NotificationResponse])
def list_notifications(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return NotificationService(db).list_for_user(current_user.id)


@router.put("/{notification_id}", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    try:
        return NotificationService(db).mark_read(notification_id, current_user.id)
    except NotificationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
