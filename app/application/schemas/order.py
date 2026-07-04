import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.application.schemas.common import ORMModel


class OrderCreateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    order_type: str = Field(default="market", pattern="^(market|limit|stop|stop_limit)$")
    side: str = Field(pattern="^(buy|sell)$")
    quantity: Decimal | None = Field(default=None, gt=0)
    requested_price: Decimal | None = Field(default=None, gt=0)
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    strategy_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _require_price_for_limit_orders(self) -> "OrderCreateRequest":
        if self.order_type != "market" and self.requested_price is None:
            raise ValueError(f"requested_price is required for {self.order_type} orders")
        if self.quantity is None and self.stop_loss is None:
            raise ValueError(
                "Provide either an explicit quantity or a stop_loss so the Risk Engine can "
                "calculate position size automatically."
            )
        return self


class RiskCheckOutcomeResponse(BaseModel):
    rule: str
    result: str
    detail: str


class OrderResponse(ORMModel):
    id: uuid.UUID
    portfolio_id: uuid.UUID
    strategy_id: uuid.UUID | None
    symbol: str
    order_type: str
    side: str
    quantity: Decimal
    requested_price: Decimal | None
    executed_price: Decimal | None
    stop_loss: Decimal | None
    take_profit: Decimal | None
    status: str
    rejection_reason: str | None
    submitted_at: dt.datetime | None
    filled_at: dt.datetime | None
    created_at: dt.datetime


class OrderCreateResponse(BaseModel):
    order: OrderResponse
    risk_checks: list[RiskCheckOutcomeResponse]
    data_source: str = Field(description="'yfinance' or 'mock' — where the execution price came from")
