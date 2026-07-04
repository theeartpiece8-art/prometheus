import datetime as dt
import uuid
from decimal import Decimal

from app.application.schemas.common import ORMModel


class PositionResponse(ORMModel):
    id: uuid.UUID
    portfolio_id: uuid.UUID
    symbol: str
    direction: str
    quantity: Decimal
    average_price: Decimal
    current_price: Decimal | None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    opened_at: dt.datetime
    closed_at: dt.datetime | None
    status: str
