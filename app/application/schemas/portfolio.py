import datetime as dt
import uuid
from decimal import Decimal

from app.application.schemas.common import ORMModel


class PortfolioResponse(ORMModel):
    id: uuid.UUID
    name: str
    balance: Decimal
    equity: Decimal
    margin_used: Decimal
    free_margin: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    kill_switch_active: bool
    peak_equity: Decimal
    created_at: dt.datetime


class EquityHistoryPointResponse(ORMModel):
    timestamp: dt.datetime
    balance: Decimal
    equity: Decimal
    drawdown: Decimal


class PortfolioExposureResponse(ORMModel):
    total_exposure: Decimal
    exposure_by_symbol: dict[str, Decimal]
    portfolio_exposure_pct: Decimal
