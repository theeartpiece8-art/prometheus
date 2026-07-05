"""
Broker domain models, per 10_Live_Trading_Engine.md's "Broker Adapter
Interface" and the Sprint 4 plan's "Broker Abstraction Layer".

Pure data + enums — no network, no framework, no broker-specific imports.
Every concrete adapter (MT5, Binance, the test mock) translates ITS OWN
wire format into these shapes and back. This is what makes "the rest of
the application must never depend directly on broker-specific APIs" (Sprint
4 plan) actually true: application/domain code only ever sees these types.
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class BrokerOrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class BrokerOrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class BrokerOrderStatus(str, Enum):
    """Per Sprint 4 plan's 'Order Synchronization' required states."""
    PENDING = "pending"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class BrokerPositionDirection(str, Enum):
    LONG = "long"
    SHORT = "short"


class BrokerConnectionStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass(frozen=True)
class BrokerOrderRequest:
    """What the application asks a broker to do. Deliberately shaped like
    the domain's existing OrderCreateRequest (app/application/schemas/
    order.py) rather than any single broker's wire format — MT5Adapter and
    BinanceAdapter each translate this into their own request shape (e.g.
    MT5's `order_send()` dict with TRADE_ACTION_DEAL/ORDER_TYPE_BUY)."""
    symbol: str
    side: BrokerOrderSide
    order_type: BrokerOrderType
    quantity: Decimal
    price: Decimal | None = None  # required for limit/stop orders, ignored for market
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    client_order_id: uuid.UUID = field(default_factory=uuid.uuid4)
    """Idempotency key generated on OUR side before the order ever reaches
    a broker — lets us recognize our own order in reconciliation even if
    the broker's own ticket/order id comes back differently, and lets a
    retried send be recognized as the same logical order."""


@dataclass(frozen=True)
class BrokerOrderResult:
    """What actually happened, per the broker. Deliberately includes both
    the requested and executed price/quantity — this is the raw material
    the Slippage Engine (Sprint 4 plan, module 11) computes from."""
    client_order_id: uuid.UUID
    broker_order_id: str | None
    status: BrokerOrderStatus
    requested_price: Decimal | None
    executed_price: Decimal | None
    requested_quantity: Decimal
    executed_quantity: Decimal
    broker_retcode: str | None = None
    """Raw broker-specific result/error code (e.g. MT5's numeric
    TRADE_RETCODE_*, Binance's string error code) -- kept for audit/
    debugging, never interpreted by application code, which only ever
    reads `status`."""
    reason: str | None = None
    filled_at: dt.datetime | None = None


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    direction: BrokerPositionDirection
    quantity: Decimal
    average_price: Decimal
    current_price: Decimal | None
    unrealized_pnl: Decimal | None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    broker_position_id: str | None = None


@dataclass(frozen=True)
class BrokerOpenOrder:
    client_order_id: uuid.UUID | None
    broker_order_id: str
    symbol: str
    side: BrokerOrderSide
    order_type: BrokerOrderType
    status: BrokerOrderStatus
    requested_price: Decimal | None
    quantity: Decimal
    filled_quantity: Decimal


@dataclass(frozen=True)
class BrokerAccountInfo:
    broker_account_id: str
    balance: Decimal
    equity: Decimal
    margin_used: Decimal
    margin_free: Decimal
    currency: str
    leverage: Decimal | None = None


@dataclass(frozen=True)
class BrokerTick:
    symbol: str
    bid: Decimal
    ask: Decimal
    timestamp: dt.datetime


@dataclass(frozen=True)
class BrokerSymbolInfo:
    """Per-instrument trading constraints — needed before PlaceOrder can
    validate/round a requested quantity to what the broker will actually
    accept (Sprint 4's Execution Validation: 'Order Size Valid, Stop
    Levels Valid')."""
    symbol: str
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    digits: int
    tradable: bool


@dataclass(frozen=True)
class BrokerHealthStatus:
    """Result of heartbeat()/health_check() -- per Sprint 4 module 8
    (Heartbeat Monitoring) and module 9 (Circuit Breakers), which key
    directly off this shape."""
    connected: bool
    latency_ms: float | None
    checked_at: dt.datetime
    detail: str | None = None


class BrokerError(Exception):
    """Base class for all broker-adapter errors. Concrete adapters raise
    subclasses; application code catches THIS (or its subclasses) and
    never a broker-specific exception type (e.g. an MT5 or Binance SDK
    exception leaking through would violate the abstraction)."""


class BrokerConnectionError(BrokerError):
    pass


class BrokerAuthenticationError(BrokerError):
    pass


class BrokerOrderRejectedError(BrokerError):
    def __init__(self, message: str, broker_retcode: str | None = None) -> None:
        super().__init__(message)
        self.broker_retcode = broker_retcode
