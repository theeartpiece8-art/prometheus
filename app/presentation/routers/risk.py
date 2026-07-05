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


# ----------------------------------------------------------------------
# Emergency Kill Switch (Sprint 4 module 10 + 07_Risk_Management_Engine.md
# "Emergency Controls"). The MECHANISM is Sprint 1's
# RiskService.trigger_kill_switch / reset_kill_switch, already enforced
# first in risk_engine.evaluate_order and already fed automatically by
# the Sprint 4 circuit breaker -- these endpoints add the MANUAL trigger
# and the operator reset. One kill switch, three triggers (manual,
# circuit breaker, risk limits), one enforcement point.
# ----------------------------------------------------------------------

from pydantic import BaseModel


class KillSwitchRequest(BaseModel):
    reason: str | None = None
    close_positions: bool = False
    """When true, every open position on the portfolio is closed
    immediately after activation -- the spec's "Optionally close open
    positions if configured" kill-switch action. Closing bypasses the
    (now-active) kill switch by design: the switch blocks NEW risk, and
    exits must always remain available. Positions are closed through the
    portfolio's standard close path; broker-side close-all for live
    positions is wired through the broker adapter layer."""


class KillSwitchResponse(BaseModel):
    kill_switch_active: bool
    positions_closed: int
    detail: str


@router.post("/kill-switch", response_model=KillSwitchResponse)
def activate_kill_switch(
    request: KillSwitchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Manual emergency stop. Idempotent: activating an already-active
    switch is safe (the original trip reason/event is preserved; a new
    event is still recorded for the audit trail)."""
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    reason = request.reason or "Manual emergency stop requested by user."
    RiskService(db).trigger_kill_switch(portfolio, reason)

    positions_closed = 0
    if request.close_positions:
        from app.application.services.order_service import OrderService

        portfolio_service = PortfolioService(db)
        order_service = OrderService(db)
        for position in portfolio_service.list_open_positions(portfolio):
            order_service.close_position(portfolio, position.id, reason="Kill Switch: Emergency Close")
            positions_closed += 1

    return KillSwitchResponse(
        kill_switch_active=True,
        positions_closed=positions_closed,
        detail=f"Kill switch activated. {positions_closed} position(s) closed." if request.close_positions
        else "Kill switch activated. Open positions preserved.",
    )


@router.post("/kill-switch/reset", response_model=KillSwitchResponse)
def reset_kill_switch(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Operator-only manual reset -- the deliberate, non-automatic
    counterpart to every automatic trigger. Also re-arms nothing else:
    a tripped circuit breaker stays tripped until ITS OWN reset, so
    resetting the kill switch while the broker is still failing will
    simply result in an immediate re-trip on the next failure."""
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    RiskService(db).reset_kill_switch(portfolio)
    return KillSwitchResponse(
        kill_switch_active=False, positions_closed=0, detail="Kill switch reset. Trading re-enabled.",
    )


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
