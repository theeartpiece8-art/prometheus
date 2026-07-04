import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel

from app.application.schemas.common import ORMModel
from app.application.schemas.order import RiskCheckOutcomeResponse


class RiskStatusResponse(BaseModel):
    equity: Decimal
    balance: Decimal
    current_drawdown_pct: Decimal
    max_drawdown_pct: Decimal
    daily_loss: Decimal
    max_daily_loss_amount: Decimal
    open_positions_count: int
    max_open_positions: int
    total_exposure: Decimal
    max_portfolio_exposure_amount: Decimal
    leverage: Decimal
    max_leverage: Decimal
    kill_switch_active: bool


class RiskEventResponse(ORMModel):
    id: uuid.UUID
    event_type: str
    description: str
    severity: str
    action_taken: str
    timestamp: dt.datetime


class RiskSettingsUpdateRequest(BaseModel):
    risk_per_trade_pct: Decimal | None = None
    max_daily_loss_pct: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    max_open_positions: int | None = None
    max_positions_per_symbol: int | None = None
    max_portfolio_exposure_pct: Decimal | None = None
    max_symbol_exposure_pct: Decimal | None = None
    max_leverage: Decimal | None = None
    min_account_balance: Decimal | None = None
    allowed_symbols: list[str] | None = None


class RiskSettingsResponse(BaseModel):
    risk_per_trade_pct: Decimal
    max_daily_loss_pct: Decimal
    max_weekly_loss_pct: Decimal
    max_monthly_loss_pct: Decimal
    max_drawdown_pct: Decimal
    max_open_positions: int
    max_positions_per_symbol: int
    max_portfolio_exposure_pct: Decimal
    max_symbol_exposure_pct: Decimal
    max_leverage: Decimal
    max_spread: Decimal | None
    max_slippage: Decimal | None
    min_account_balance: Decimal
    allowed_symbols: list[str] | None
    allowed_trading_sessions: list[str] | None


class OrderPreviewRequest(BaseModel):
    """Lets the UI ask 'would this order be approved?' without submitting it."""
    symbol: str
    side: str
    order_type: str = "market"
    requested_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    quantity: Decimal | None = None
    strategy_id: uuid.UUID | None = None


class OrderPreviewResponse(BaseModel):
    would_approve: bool
    reason: str | None
    approved_position_size: Decimal | None
    checks: list[RiskCheckOutcomeResponse]
