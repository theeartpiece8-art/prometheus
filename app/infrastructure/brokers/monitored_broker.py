"""
MonitoredBrokerAdapter: a decorator around any BrokerAdapter that times
every call, feeds a CircuitBreaker, and fires an on_trip callback exactly
once when the breaker opens. This is the enforcement point for Sprint 4
plan module 9 -- LiveExecutionEngine doesn't know it exists (it just
receives a BrokerAdapter), and the wrapped adapter doesn't either.

Two safety distinctions, deliberate and load-bearing:

1. BrokerOrderRejectedError does NOT count as a breaker failure. A
   rejection (insufficient margin, invalid volume) is the broker WORKING
   CORRECTLY -- tripping on it would let a run of legitimately-rejected
   orders shut down connectivity that is perfectly healthy. Connection
   errors and unexpected exceptions count; business rejections pass
   through untouched. ("Repeated Order Rejections" as a kill-switch
   trigger in 10_Live_Trading_Engine.md is about the RISK side -- the
   Risk Engine's own rejection tracking -- not transport health.)

2. An OPEN breaker blocks NEW risk only: place_order and modify_order.
   close_position, close_all, cancel_order, disconnect, and every read
   remain allowed -- the operator (and the kill switch's close-all
   action) must ALWAYS be able to reduce risk and observe state, exactly
   mirroring LiveExecutionEngine.close_position's bypass of the
   live_trading_enabled gate.
"""
from __future__ import annotations

import time
from typing import Callable

from app.domain.broker.broker_interface import BrokerAdapter
from app.domain.broker.broker_models import (
    BrokerAccountInfo,
    BrokerConnectionError,
    BrokerError,
    BrokerHealthStatus,
    BrokerOpenOrder,
    BrokerOrderRejectedError,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerPosition,
    BrokerSymbolInfo,
    BrokerTick,
)
from app.domain.broker.circuit_breaker import CircuitBreaker


class CircuitOpenError(BrokerError):
    """Raised when a blocked operation is attempted while the breaker is
    open. Subclasses BrokerError so existing callers' error handling
    (LiveExecutionEngine catches BrokerError subclasses) already copes."""


class MonitoredBrokerAdapter(BrokerAdapter):
    def __init__(
        self,
        inner: BrokerAdapter,
        breaker: CircuitBreaker | None = None,
        on_trip: Callable[[str], None] | None = None,
    ) -> None:
        self.inner = inner
        self.breaker = breaker or CircuitBreaker()
        self._on_trip = on_trip
        self._trip_notified = False

    # ------------------------------------------------------------------

    def _guard_new_risk(self) -> None:
        if self.breaker.is_open:
            raise CircuitOpenError(
                f"Circuit breaker is open ({self.breaker.trip_reason}). "
                "New orders are blocked; closing positions remains available. Manual reset required."
            )

    def _observed(self, fn: Callable, *args, **kwargs):
        """Run an inner-adapter call under observation: time it, feed the
        breaker, fire on_trip exactly once if this call tripped it."""
        started = time.monotonic()
        try:
            result = fn(*args, **kwargs)
        except BrokerOrderRejectedError:
            # Business rejection: the broker is healthy. Counts as a
            # SUCCESS for connectivity purposes (resets failure streak).
            self.breaker.record_success((time.monotonic() - started) * 1000)
            self._maybe_notify_trip()
            raise
        except BrokerError as exc:
            self.breaker.record_failure(str(exc))
            self._maybe_notify_trip()
            raise
        latency_ms = (time.monotonic() - started) * 1000
        self.breaker.record_success(latency_ms)
        self._maybe_notify_trip()  # a latency-pattern trip can happen on a "successful" call
        return result

    def _maybe_notify_trip(self) -> None:
        if self.breaker.is_open and not self._trip_notified:
            self._trip_notified = True
            if self._on_trip is not None:
                self._on_trip(self.breaker.trip_reason or "Circuit breaker tripped.")

    def reset(self) -> None:
        """Operator-initiated reset: re-arms both the breaker and the
        one-shot trip notification."""
        self.breaker.reset()
        self._trip_notified = False

    # ------------------------------------------------------------------
    # BrokerAdapter interface
    # ------------------------------------------------------------------

    @property
    def broker_name(self) -> str:
        return self.inner.broker_name

    def connect(self) -> None:
        self._observed(self.inner.connect)

    def disconnect(self) -> None:
        # Never guarded, never counted: deliberately disconnecting is not a failure.
        self.inner.disconnect()

    def reconnect(self) -> None:
        self._observed(self.inner.reconnect)

    def is_connected(self) -> bool:
        return self.inner.is_connected()

    def health_check(self) -> BrokerHealthStatus:
        """Health probes feed the breaker like any other observed call --
        this is how the heartbeat monitor's periodic checks accumulate
        into a trip -- and a probe that RETURNS 'not connected' (rather
        than raising) is recorded as a disconnect, which trips
        immediately."""
        status = self._observed(self.inner.health_check)
        if not status.connected:
            self.breaker.record_disconnect("Health check reported broker not connected.")
            self._maybe_notify_trip()
        return status

    def get_account(self) -> BrokerAccountInfo:
        return self._observed(self.inner.get_account)

    def get_positions(self, symbol: str | None = None) -> list[BrokerPosition]:
        return self._observed(self.inner.get_positions, symbol)

    def get_orders(self, symbol: str | None = None) -> list[BrokerOpenOrder]:
        return self._observed(self.inner.get_orders, symbol)

    def get_symbols(self) -> list[BrokerSymbolInfo]:
        return self._observed(self.inner.get_symbols)

    def get_tick(self, symbol: str) -> BrokerTick:
        return self._observed(self.inner.get_tick, symbol)

    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        self._guard_new_risk()
        return self._observed(self.inner.place_order, request)

    def modify_order(self, broker_order_id: str, *, price=None, stop_loss=None, take_profit=None) -> BrokerOrderResult:
        self._guard_new_risk()
        return self._observed(
            self.inner.modify_order, broker_order_id, price=price, stop_loss=stop_loss, take_profit=take_profit
        )

    def cancel_order(self, broker_order_id: str) -> None:
        # Risk-reducing: allowed even when open.
        self._observed(self.inner.cancel_order, broker_order_id)

    def close_position(self, broker_position_id: str, quantity=None) -> BrokerOrderResult:
        # Risk-reducing: allowed even when open.
        return self._observed(self.inner.close_position, broker_position_id, quantity)

    def close_all(self) -> list[BrokerOrderResult]:
        # THE kill-switch action: must work precisely when things are on fire.
        return self._observed(self.inner.close_all)
