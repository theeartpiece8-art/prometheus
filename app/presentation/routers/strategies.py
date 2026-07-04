import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.application.schemas.common import MessageResponse
from app.application.schemas.strategy import StrategyCreateRequest, StrategyResponse, StrategyUpdateRequest
from app.application.services.strategy_service import (
    InvalidStrategyParametersError,
    StrategyNotFoundError,
    StrategyService,
)
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.user import User

router = APIRouter(prefix="/strategies", tags=["Strategies"])


@router.get("", response_model=list[StrategyResponse])
def list_strategies(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return StrategyService(db).list_for_user(current_user.id)


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
def create_strategy(
    payload: StrategyCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    try:
        return StrategyService(db).create(
            current_user.id, payload.name, payload.strategy_type, payload.description,
            payload.asset_class, payload.timeframe, payload.parameters,
        )
    except InvalidStrategyParametersError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.get("/{strategy_id}", response_model=StrategyResponse)
def get_strategy(strategy_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    try:
        return StrategyService(db).get(strategy_id, current_user.id)
    except StrategyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.put("/{strategy_id}", response_model=StrategyResponse)
def update_strategy(
    strategy_id: uuid.UUID, payload: StrategyUpdateRequest,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user),
):
    try:
        return StrategyService(db).update(strategy_id, current_user.id, **payload.model_dump(exclude_unset=True))
    except StrategyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidStrategyParametersError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.delete("/{strategy_id}", response_model=MessageResponse)
def delete_strategy(strategy_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    try:
        StrategyService(db).delete(strategy_id, current_user.id)
    except StrategyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return MessageResponse(detail="Strategy deleted.")


@router.post("/{strategy_id}/clone", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
def clone_strategy(strategy_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    try:
        return StrategyService(db).clone(strategy_id, current_user.id)
    except StrategyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{strategy_id}/enable", response_model=StrategyResponse)
def enable_strategy(strategy_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    try:
        return StrategyService(db).set_enabled(strategy_id, current_user.id, True)
    except StrategyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{strategy_id}/disable", response_model=StrategyResponse)
def disable_strategy(strategy_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    try:
        return StrategyService(db).set_enabled(strategy_id, current_user.id, False)
    except StrategyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
