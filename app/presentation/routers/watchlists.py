import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.application.schemas.common import MessageResponse
from app.application.schemas.watchlist import WatchlistCreateRequest, WatchlistResponse, WatchlistUpdateRequest
from app.application.services.watchlist_service import WatchlistNotFoundError, WatchlistService
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.user import User

router = APIRouter(prefix="/watchlists", tags=["Watchlists"])


@router.get("", response_model=list[WatchlistResponse])
def list_watchlists(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return WatchlistService(db).list_for_user(current_user.id)


@router.post("", response_model=WatchlistResponse, status_code=status.HTTP_201_CREATED)
def create_watchlist(
    payload: WatchlistCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    return WatchlistService(db).create(current_user.id, payload.name, payload.symbols)


@router.put("/{watchlist_id}", response_model=WatchlistResponse)
def update_watchlist(
    watchlist_id: uuid.UUID, payload: WatchlistUpdateRequest,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user),
):
    try:
        return WatchlistService(db).update(watchlist_id, current_user.id, **payload.model_dump(exclude_unset=True))
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{watchlist_id}", response_model=MessageResponse)
def delete_watchlist(
    watchlist_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    try:
        WatchlistService(db).delete(watchlist_id, current_user.id)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MessageResponse(detail="Watchlist deleted.")
