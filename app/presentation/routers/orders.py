import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.application.schemas.order import OrderCreateRequest, OrderCreateResponse, OrderResponse, RiskCheckOutcomeResponse
from app.application.services.order_service import OrderService, OrderServiceError, StrategyNotFoundError
from app.application.services.portfolio_service import PortfolioService
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.enums import OrderStatus
from app.infrastructure.models.user import User
from app.infrastructure.repositories.order_repository import OrderRepository

router = APIRouter(prefix="/orders", tags=["Orders"])


@router.get("", response_model=list[OrderResponse])
def list_orders(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    return OrderRepository(db).list_for_portfolio(portfolio.id)


@router.get("/open", response_model=list[OrderResponse])
def list_open_orders(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    return OrderRepository(db).list_open_for_portfolio(portfolio.id)


@router.post("/place", response_model=OrderCreateResponse, status_code=status.HTTP_201_CREATED)
def place_order(
    payload: OrderCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    try:
        order, checks, data_source = OrderService(db).create_order(current_user.id, portfolio, payload)
    except StrategyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return OrderCreateResponse(
        order=OrderResponse.model_validate(order),
        risk_checks=[RiskCheckOutcomeResponse(rule=c.rule, result=c.result.value, detail=c.detail) for c in checks],
        data_source=data_source,
    )


@router.delete("/{order_id}", response_model=OrderResponse)
def cancel_order(order_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    repo = OrderRepository(db)
    order = repo.get(order_id)
    if order is None or order.portfolio_id != portfolio.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")
    if order.status not in (OrderStatus.PENDING.value, OrderStatus.APPROVED.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel an order with status '{order.status}'. "
            "Sprint 1 orders resolve (fill or reject) synchronously, so pending "
            "orders eligible for cancellation should not normally exist yet — this "
            "endpoint is here for forward-compatibility with limit/stop orders.",
        )
    repo.update(order, status=OrderStatus.CANCELLED.value)
    db.commit()
    db.refresh(order)
    return order
