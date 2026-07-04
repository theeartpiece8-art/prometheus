import datetime as dt
import uuid

from pydantic import BaseModel, Field, field_validator

MIN_TICK_INTERVAL_SECONDS = 15
"""Floor enforced to avoid hammering the market data provider (and any
real yfinance rate limits) — same proportionate-safeguard pattern as
Sprint 2's MAX_BACKTEST_BARS."""


class SessionItemRequest(BaseModel):
    strategy_id: uuid.UUID
    symbol: str = Field(min_length=1, max_length=32)
    timeframe: str = Field(default="1D")


class StartSessionRequest(BaseModel):
    items: list[SessionItemRequest] = Field(min_length=1, description="One or more (strategy, symbol) pairs to run")
    tick_interval_seconds: int = Field(default=60, ge=MIN_TICK_INTERVAL_SECONDS, le=3600)

    @field_validator("items")
    @classmethod
    def _no_duplicate_strategy_symbol_pairs(cls, v: list[SessionItemRequest]) -> list[SessionItemRequest]:
        seen = set()
        for item in v:
            key = (item.strategy_id, item.symbol, item.timeframe)
            if key in seen:
                raise ValueError(f"Duplicate (strategy_id, symbol, timeframe) entry: {key}")
            seen.add(key)
        return v


class SessionItemResponse(BaseModel):
    id: uuid.UUID
    strategy_id: uuid.UUID
    strategy_name: str
    symbol: str
    timeframe: str


class SessionResponse(BaseModel):
    id: uuid.UUID
    status: str
    tick_interval_seconds: int
    status_reason: str | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    paused_at: dt.datetime | None
    stopped_at: dt.datetime | None
    last_tick_at: dt.datetime | None
    tick_count: int
    items: list[SessionItemResponse]


class TickRejectionSummary(BaseModel):
    strategy_id: uuid.UUID
    symbol: str
    reason: str


class TickActionSummary(BaseModel):
    strategy_id: uuid.UUID
    symbol: str
    action: str  # "opened" | "closed_signal_reversal" | "closed_stop_loss" | "closed_take_profit"
    order_id: uuid.UUID | None = None


class TickResultResponse(BaseModel):
    session_id: uuid.UUID
    ticked_at: dt.datetime
    items_evaluated: int
    actions: list[TickActionSummary]
    rejections: list[TickRejectionSummary]
    data_feed_ok: bool


class StrategyMonitorResponse(BaseModel):
    """09_Paper_Trading_Engine.md 'Strategy Monitoring' section."""
    strategy_id: uuid.UUID
    strategy_name: str
    symbol: str
    status: str
    current_position: str | None  # "long" | "short" | None
    number_of_trades: int
    win_rate: float | None
    profit_factor: float | None
    current_drawdown_pct: float
    running_pnl: float


class PaperTradeResponse(BaseModel):
    id: uuid.UUID
    symbol: str
    strategy_id: uuid.UUID | None
    entry_price: float
    exit_price: float | None
    quantity: float
    net_profit: float | None
    commission: float
    outcome: str | None
    created_at: dt.datetime


def build_session_response(session) -> SessionResponse:
    """Maps a PaperTradingSession ORM row (+ eager items w/ strategies) to
    the API shape. Pure data reshaping — kept out of the router per the
    'no logic in controllers' coding standard, and out of the service
    because HTTP response shape is a presentation concern."""
    return SessionResponse(
        id=session.id,
        status=session.status,
        tick_interval_seconds=session.tick_interval_seconds,
        status_reason=session.status_reason,
        created_at=session.created_at,
        started_at=session.started_at,
        paused_at=session.paused_at,
        stopped_at=session.stopped_at,
        last_tick_at=session.last_tick_at,
        tick_count=session.tick_count,
        items=[
            SessionItemResponse(
                id=item.id,
                strategy_id=item.strategy_id,
                strategy_name=item.strategy.name if item.strategy else "(deleted)",
                symbol=item.symbol,
                timeframe=item.timeframe,
            )
            for item in session.items
        ],
    )


def build_trade_response(trade) -> PaperTradeResponse:
    return PaperTradeResponse(
        id=trade.id,
        symbol=trade.symbol,
        strategy_id=trade.strategy_id,
        entry_price=float(trade.entry_price),
        exit_price=float(trade.exit_price) if trade.exit_price is not None else None,
        quantity=float(trade.quantity),
        net_profit=float(trade.net_profit) if trade.net_profit is not None else None,
        commission=float(trade.commission),
        outcome=trade.outcome,
        created_at=trade.created_at,
    )
