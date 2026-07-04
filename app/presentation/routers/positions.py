import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.application.schemas.position import PositionResponse
from app.application.services.order_service import OrderService, OrderServiceError
from app.application.services.portfolio_service import PortfolioService
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.user import User

router = APIRouter(prefix="/positions", tags=["Positions"])


@router.get("", response_model=list[PositionResponse])
def list_positions(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    return PortfolioService(db).list_open_positions(portfolio)


@router.post("/close/{position_id}", response_model=PositionResponse)
def close_position(position_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    try:
        return OrderService(db).close_position(portfolio, position_id)
    except OrderServiceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/close-all", response_model=list[PositionResponse])
def close_all_positions(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Emergency close all positions — per 05_API_Specification.md."""
    portfolio_service = PortfolioService(db)
    portfolio = portfolio_service.get_default_for_user(current_user.id)
    order_service = OrderService(db)
    closed = []
    for position in portfolio_service.list_open_positions(portfolio):
        closed.append(order_service.close_position(portfolio, position.id))
    return closed
