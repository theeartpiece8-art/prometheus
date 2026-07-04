from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.application.schemas.risk import (
    OrderPreviewRequest,
    OrderPreviewResponse,
    RiskCheckOutcomeResponse,
    RiskEventResponse,
    RiskSettingsResponse,
    RiskSettingsUpdateRequest,
    RiskStatusResponse,
)
from app.application.services.portfolio_service import PortfolioService
from app.application.services.risk_service import RiskService
from app.application.services.settings_service import SettingsService
from app.core.decimal_utils import clean_decimal
from app.core.dependencies import get_current_active_user, get_db
from app.domain.risk.risk_models import OrderRequest as DomainOrderRequest
from app.infrastructure.models.user import User
from app.infrastructure.repositories.risk_event_repository import RiskEventRepository

router = APIRouter(prefix="/risk", tags=["Risk Engine"])


@router.get("/status", response_model=RiskStatusResponse)
def get_risk_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    portfolio_service = PortfolioService(db)
    risk_service = RiskService(db)
    portfolio = portfolio_service.get_default_for_user(current_user.id)
    settings = risk_service.load_risk_settings(current_user.id)
    account = risk_service.build_account_state(portfolio, symbol="__PORTFOLIO_LEVEL__")

    from decimal import Decimal

    leverage = clean_decimal(account.current_portfolio_exposure / account.equity) if account.equity > 0 else Decimal("0")

    return RiskStatusResponse(
        equity=portfolio.equity,
        balance=portfolio.balance,
        current_drawdown_pct=clean_decimal(account.current_drawdown_pct),
        max_drawdown_pct=settings.max_drawdown_pct,
        daily_loss=account.current_daily_loss,
        max_daily_loss_amount=clean_decimal(portfolio.equity * settings.max_daily_loss_pct / 100),
        open_positions_count=account.open_positions_count,
        max_open_positions=settings.max_open_positions,
        total_exposure=account.current_portfolio_exposure,
        max_portfolio_exposure_amount=clean_decimal(portfolio.equity * settings.max_portfolio_exposure_pct / 100),
        leverage=leverage,
        max_leverage=settings.max_leverage,
        kill_switch_active=portfolio.kill_switch_active,
    )


@router.get("/events", response_model=list[RiskEventResponse])
def get_risk_events(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    return RiskEventRepository(db).list_for_portfolio(portfolio.id)


@router.put("/settings", response_model=RiskSettingsResponse)
def update_risk_settings(
    payload: RiskSettingsUpdateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    settings_service = SettingsService(db)
    settings_service.update_risk_settings(current_user.id, payload.model_dump(exclude_unset=True))
    updated = RiskService(db).load_risk_settings(current_user.id)
    return _to_settings_response(updated)


@router.get("/settings", response_model=RiskSettingsResponse)
def get_risk_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    settings = RiskService(db).load_risk_settings(current_user.id)
    return _to_settings_response(settings)


@router.post("/preview", response_model=OrderPreviewResponse)
def preview_order(
    payload: OrderPreviewRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """Lets the UI check whether an order WOULD be approved, without submitting it."""
    from app.infrastructure.market_data.provider import get_latest_price

    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    risk_service = RiskService(db)

    # Mirror OrderService.create_order's Market Condition Check: market orders
    # are priced off the live quote, not whatever (or nothing) the client sent.
    price = payload.requested_price
    if payload.order_type == "market":
        price, _data_source = get_latest_price(payload.symbol)

    domain_request = DomainOrderRequest(
        symbol=payload.symbol, side=payload.side, order_type=payload.order_type,
        requested_price=price, stop_loss=payload.stop_loss,
        take_profit=payload.take_profit, quantity=payload.quantity, strategy_enabled=True,
    )
    account = risk_service.build_account_state(portfolio, payload.symbol)
    settings = risk_service.load_risk_settings(current_user.id)

    from app.domain.risk.risk_engine import risk_engine

    decision = risk_engine.evaluate_order(domain_request, account, settings)
    # NOTE: preview never calls risk_service.evaluate() directly, since that
    # method logs + records a RiskEvent audit row on rejection. A preview is
    # explicitly NOT a real order attempt, so it must not pollute the audit log.
    return OrderPreviewResponse(
        would_approve=decision.approved,
        reason=decision.reason,
        approved_position_size=decision.approved_position_size,
        checks=[RiskCheckOutcomeResponse(rule=c.rule, result=c.result.value, detail=c.detail) for c in decision.checks],
    )


def _to_settings_response(settings) -> RiskSettingsResponse:
    return RiskSettingsResponse(
        risk_per_trade_pct=settings.risk_per_trade_pct,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_weekly_loss_pct=settings.max_daily_loss_pct * 3,
        max_monthly_loss_pct=settings.max_daily_loss_pct * 8,
        max_drawdown_pct=settings.max_drawdown_pct,
        max_open_positions=settings.max_open_positions,
        max_positions_per_symbol=settings.max_positions_per_symbol,
        max_portfolio_exposure_pct=settings.max_portfolio_exposure_pct,
        max_symbol_exposure_pct=settings.max_symbol_exposure_pct,
        max_leverage=settings.max_leverage,
        max_spread=None,
        max_slippage=None,
        min_account_balance=settings.min_account_balance,
        allowed_symbols=settings.allowed_symbols,
        allowed_trading_sessions=None,
    )
