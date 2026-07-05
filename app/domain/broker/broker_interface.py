"""
Broker Adapter interface, per 10_Live_Trading_Engine.md's "Broker Adapter
Interface" section — which specifies a fuller method list (GetBalance,
GetSymbols, GetTick, ClosePosition, CloseAll, Reconnect as methods
distinct from Connect/Disconnect) than the Sprint 4 plan's shorter
restatement. Built to the fuller original spec; this is the "Broker
Abstraction Layer" (Sprint 4 plan, module 1).

"The rest of the application must never depend directly on broker-specific
APIs" (Sprint 4 plan) is enforced structurally here: every method takes
and returns ONLY the pure dataclasses from broker_models.py, never a
broker SDK's own types.
"""
from __future__ import annotations

import abc
import uuid

from app.domain.broker.broker_models import (
    BrokerAccountInfo,
    BrokerHealthStatus,
    BrokerOpenOrder,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerPosition,
    BrokerSymbolInfo,
    BrokerTick,
)


class BrokerAdapter(abc.ABC):
    """Every method may raise BrokerError (or a subclass) — callers
    (LiveExecutionEngine, reconciliation, health checks) are the ones
    responsible for catching these and routing to the Risk Engine /
    Circuit Breaker / notification layer as appropriate. An adapter must
    NEVER swallow an error and return a fabricated success result."""

    @property
    @abc.abstractmethod
    def broker_name(self) -> str:
        """Short, stable identifier (e.g. 'mt5', 'binance') — used in
        logs, audit trails, and to disambiguate which adapter a
        BrokerAccount row refers to."""

    @abc.abstractmethod
    def connect(self) -> None:
        """Establish a connection/session. Idempotent: calling connect()
        while already connected must be a safe no-op, not an error."""

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Tear down the connection cleanly. Idempotent."""

    @abc.abstractmethod
    def reconnect(self) -> None:
        """Per the spec: reconnection is a DISTINCT operation from a
        fresh connect() -- it may need to re-authenticate, re-subscribe
        to symbols, and reconcile state that changed while disconnected,
        none of which a plain connect() call is responsible for."""

    @abc.abstractmethod
    def is_connected(self) -> bool:
        """Cheap, local state check (not a network round-trip) -- use
        health_check() when you need to confirm the connection is
        actually alive right now, not just that it was established."""

    @abc.abstractmethod
    def health_check(self) -> BrokerHealthStatus:
        """An actual round-trip probe. Backs Sprint 4 module 8 (Heartbeat
        Monitoring) and module 9 (Circuit Breakers) -- both key off this,
        not off is_connected()."""

    @abc.abstractmethod
    def get_account(self) -> BrokerAccountInfo:
        pass

    @abc.abstractmethod
    def get_positions(self, symbol: str | None = None) -> list[BrokerPosition]:
        pass

    @abc.abstractmethod
    def get_orders(self, symbol: str | None = None) -> list[BrokerOpenOrder]:
        pass

    @abc.abstractmethod
    def get_symbols(self) -> list[BrokerSymbolInfo]:
        pass

    @abc.abstractmethod
    def get_tick(self, symbol: str) -> BrokerTick:
        pass

    @abc.abstractmethod
    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        """MUST NOT be called directly by any code path that hasn't
        already passed the order through the Risk Engine -- this
        interface has no way to enforce that itself (it's a pure
        execution boundary), so the enforcement point is
        LiveExecutionEngine, the only intended caller. Every adapter
        implementation must raise BrokerOrderRejectedError (never return
        a fabricated 'filled' result) if the broker itself rejects the
        order."""

    @abc.abstractmethod
    def modify_order(
        self, broker_order_id: str, *, price: object = None, stop_loss: object = None, take_profit: object = None,
    ) -> BrokerOrderResult:
        pass

    @abc.abstractmethod
    def cancel_order(self, broker_order_id: str) -> None:
        pass

    @abc.abstractmethod
    def close_position(self, broker_position_id: str, quantity: object = None) -> BrokerOrderResult:
        """`quantity=None` closes the full position; a value closes
        partially -- per Sprint 4's Partial Fill / partial-close
        requirements."""

    @abc.abstractmethod
    def close_all(self) -> list[BrokerOrderResult]:
        """Backs the Emergency Kill Switch's 'cancel pending orders /
        close positions where supported' action. Must attempt every
        position even if one fails -- return one BrokerOrderResult per
        position attempted, not raise on the first failure."""
