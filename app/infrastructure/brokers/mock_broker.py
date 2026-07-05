"""
MockBrokerAdapter: an in-memory, fully controllable BrokerAdapter
implementation used to test the Live Execution Engine, position/order
sync, heartbeat, circuit breakers, and kill switch -- everything that
sits ABOVE the broker interface -- without any real broker connectivity.

This is not a throwaway test double defined inline in one test file (the
Sprint 3 pattern for simple cases): it's promoted to a real, reusable
infrastructure module because it needs to be shared across many test
files covering many Sprint 4 modules, and because its FAILURE-INJECTION
knobs (simulate_disconnect, reject_next_n_orders, latency_ms) are
themselves part of what Sprint 4's "Failure Recovery Tests" / "Reconnect
Tests" / "Circuit Breaker" tests need to control precisely.
"""
from __future__ import annotations

import datetime as dt
import time
import uuid
from decimal import Decimal

from app.domain.broker.broker_interface import BrokerAdapter
from app.domain.broker.broker_models import (
    BrokerAccountInfo,
    BrokerConnectionError,
    BrokerConnectionStatus,
    BrokerHealthStatus,
    BrokerOpenOrder,
    BrokerOrderRejectedError,
    BrokerOrderRequest,
    BrokerOrderResult,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerPosition,
    BrokerPositionDirection,
    BrokerSymbolInfo,
    BrokerTick,
)


class MockBrokerAdapter(BrokerAdapter):
    def __init__(
        self,
        *,
        starting_balance: Decimal = Decimal("10000"),
        tick_price: Decimal = Decimal("100"),
        connect_should_fail: bool = False,
    ) -> None:
        self._status = BrokerConnectionStatus.DISCONNECTED
        self._balance = starting_balance
        self._tick_price = tick_price
        self._connect_should_fail = connect_should_fail
        self._positions: dict[str, BrokerPosition] = {}
        self._orders: dict[str, BrokerOpenOrder] = {}
        self._next_ticket = 1000
        self._latency_ms = 5.0

        # Failure-injection knobs, set directly by tests:
        self.reject_next_n_orders = 0
        self.reject_reason = "Mock rejection"
        self.raise_on_health_check = False
        self.tick_should_fail = False

    # ------------------------------------------------------------------

    @property
    def broker_name(self) -> str:
        return "mock"

    def connect(self) -> None:
        if self._connect_should_fail:
            self._status = BrokerConnectionStatus.FAILED
            raise BrokerConnectionError("Mock broker configured to fail connection.")
        self._status = BrokerConnectionStatus.CONNECTED

    def disconnect(self) -> None:
        self._status = BrokerConnectionStatus.DISCONNECTED

    def reconnect(self) -> None:
        self._status = BrokerConnectionStatus.RECONNECTING
        self.connect()

    def is_connected(self) -> bool:
        return self._status == BrokerConnectionStatus.CONNECTED

    def health_check(self) -> BrokerHealthStatus:
        if self.raise_on_health_check:
            raise BrokerConnectionError("Mock broker health check configured to fail.")
        return BrokerHealthStatus(
            connected=self.is_connected(), latency_ms=self._latency_ms, checked_at=dt.datetime.now(dt.timezone.utc),
        )

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise BrokerConnectionError("Mock broker is not connected.")

    def get_account(self) -> BrokerAccountInfo:
        self._require_connected()
        equity = self._balance + sum((p.unrealized_pnl or Decimal("0")) for p in self._positions.values())
        return BrokerAccountInfo(
            broker_account_id="mock-account-1", balance=self._balance, equity=equity,
            margin_used=Decimal("0"), margin_free=equity, currency="USD",
        )

    def get_positions(self, symbol: str | None = None) -> list[BrokerPosition]:
        self._require_connected()
        values = list(self._positions.values())
        return [p for p in values if symbol is None or p.symbol == symbol]

    def get_orders(self, symbol: str | None = None) -> list[BrokerOpenOrder]:
        self._require_connected()
        values = list(self._orders.values())
        return [o for o in values if symbol is None or o.symbol == symbol]

    def get_symbols(self) -> list[BrokerSymbolInfo]:
        self._require_connected()
        return [
            BrokerSymbolInfo(
                symbol=s, volume_min=Decimal("0.01"), volume_max=Decimal("100"),
                volume_step=Decimal("0.01"), digits=2, tradable=True,
            )
            for s in ("EURUSD", "BTCUSD", "AAPL")
        ]

    def get_tick(self, symbol: str) -> BrokerTick:
        self._require_connected()
        if self.tick_should_fail:
            raise BrokerConnectionError(f"Mock tick fetch failed for {symbol}.")
        spread = self._tick_price * Decimal("0.0001")
        return BrokerTick(
            symbol=symbol, bid=self._tick_price - spread, ask=self._tick_price + spread,
            timestamp=dt.datetime.now(dt.timezone.utc),
        )

    def place_order(self, request: BrokerOrderRequest) -> BrokerOrderResult:
        self._require_connected()
        time.sleep(self._latency_ms / 1000)

        if self.reject_next_n_orders > 0:
            self.reject_next_n_orders -= 1
            raise BrokerOrderRejectedError(self.reject_reason, broker_retcode="MOCK_REJECT")

        fill_price = request.price or self._tick_price
        ticket = str(self._next_ticket)
        self._next_ticket += 1

        existing = self._positions.get(request.symbol)
        incoming_direction = (
            BrokerPositionDirection.LONG if request.side == BrokerOrderSide.BUY else BrokerPositionDirection.SHORT
        )
        if existing is None:
            self._positions[request.symbol] = BrokerPosition(
                symbol=request.symbol, direction=incoming_direction, quantity=request.quantity,
                average_price=fill_price, current_price=fill_price, unrealized_pnl=Decimal("0"),
                stop_loss=request.stop_loss, take_profit=request.take_profit, broker_position_id=ticket,
            )
        elif existing.direction == incoming_direction:
            total_qty = existing.quantity + request.quantity
            new_avg = ((existing.average_price * existing.quantity) + (fill_price * request.quantity)) / total_qty
            self._positions[request.symbol] = BrokerPosition(
                symbol=request.symbol, direction=existing.direction, quantity=total_qty,
                average_price=new_avg, current_price=fill_price, unrealized_pnl=Decimal("0"),
                stop_loss=request.stop_loss or existing.stop_loss, take_profit=request.take_profit or existing.take_profit,
                broker_position_id=existing.broker_position_id,
            )
        else:
            closing_qty = min(existing.quantity, request.quantity)
            remaining_existing = existing.quantity - closing_qty
            if remaining_existing > 0:
                # Partial reduce: existing position stays open, smaller.
                self._positions[request.symbol] = BrokerPosition(
                    symbol=request.symbol, direction=existing.direction, quantity=remaining_existing,
                    average_price=existing.average_price, current_price=fill_price, unrealized_pnl=Decimal("0"),
                    stop_loss=existing.stop_loss, take_profit=existing.take_profit,
                    broker_position_id=existing.broker_position_id,
                )
            else:
                del self._positions[request.symbol]
                remainder = request.quantity - closing_qty
                if remainder > 0:
                    # Flip: the incoming order more than closed the existing
                    # position -- open a new one in the opposite direction
                    # with whatever's left over, under a fresh ticket.
                    flip_ticket = str(self._next_ticket)
                    self._next_ticket += 1
                    self._positions[request.symbol] = BrokerPosition(
                        symbol=request.symbol, direction=incoming_direction, quantity=remainder,
                        average_price=fill_price, current_price=fill_price, unrealized_pnl=Decimal("0"),
                        stop_loss=request.stop_loss, take_profit=request.take_profit,
                        broker_position_id=flip_ticket,
                    )

        return BrokerOrderResult(
            client_order_id=request.client_order_id, broker_order_id=ticket, status=BrokerOrderStatus.FILLED,
            requested_price=request.price, executed_price=fill_price, requested_quantity=request.quantity,
            executed_quantity=request.quantity, broker_retcode="MOCK_DONE",
            filled_at=dt.datetime.now(dt.timezone.utc),
        )

    def modify_order(self, broker_order_id, *, price=None, stop_loss=None, take_profit=None) -> BrokerOrderResult:
        self._require_connected()
        order = self._orders.get(broker_order_id)
        if order is None:
            raise BrokerOrderRejectedError(f"No such order: {broker_order_id}")
        return BrokerOrderResult(
            client_order_id=order.client_order_id or uuid.uuid4(), broker_order_id=broker_order_id,
            status=order.status, requested_price=price, executed_price=None,
            requested_quantity=order.quantity, executed_quantity=order.filled_quantity,
        )

    def cancel_order(self, broker_order_id: str) -> None:
        self._require_connected()
        self._orders.pop(broker_order_id, None)

    def close_position(self, broker_position_id: str, quantity=None) -> BrokerOrderResult:
        self._require_connected()
        match = next((p for p in self._positions.values() if p.broker_position_id == broker_position_id), None)
        if match is None:
            raise BrokerOrderRejectedError(f"No such position: {broker_position_id}")

        close_qty = quantity or match.quantity
        opposite_side = BrokerOrderSide.SELL if match.direction == BrokerPositionDirection.LONG else BrokerOrderSide.BUY
        result = self.place_order(
            BrokerOrderRequest(symbol=match.symbol, side=opposite_side, order_type=BrokerOrderType.MARKET, quantity=close_qty)
        )
        return result

    def close_all(self) -> list[BrokerOrderResult]:
        self._require_connected()
        results = []
        for position in list(self._positions.values()):
            try:
                results.append(self.close_position(position.broker_position_id))
            except BrokerOrderRejectedError as exc:  # pragma: no cover -- close_all must never abort on one failure
                results.append(
                    BrokerOrderResult(
                        client_order_id=uuid.uuid4(), broker_order_id=position.broker_position_id,
                        status=BrokerOrderStatus.REJECTED, requested_price=None, executed_price=None,
                        requested_quantity=position.quantity, executed_quantity=Decimal("0"), reason=str(exc),
                    )
                )
        return results

    # ------------------------------------------------------------------
    # Test-only helpers (not part of the BrokerAdapter interface)
    # ------------------------------------------------------------------

    def set_tick_price(self, price: Decimal) -> None:
        """Move 'the market' for tests, and mark unrealized_pnl on any
        open position accordingly."""
        self._tick_price = price
        for symbol, pos in list(self._positions.items()):
            if pos.direction == BrokerPositionDirection.LONG:
                pnl = (price - pos.average_price) * pos.quantity
            else:
                pnl = (pos.average_price - price) * pos.quantity
            self._positions[symbol] = BrokerPosition(
                symbol=pos.symbol, direction=pos.direction, quantity=pos.quantity,
                average_price=pos.average_price, current_price=price, unrealized_pnl=pnl,
                stop_loss=pos.stop_loss, take_profit=pos.take_profit, broker_position_id=pos.broker_position_id,
            )

    def simulate_disconnect(self) -> None:
        self._status = BrokerConnectionStatus.DISCONNECTED

    def inject_open_order(
        self, *, symbol: str, side: BrokerOrderSide, quantity: Decimal,
        price: Decimal | None = None, client_order_id: uuid.UUID | None = None,
        filled_quantity: Decimal = Decimal("0"),
    ) -> str:
        """Test-only: place an OPEN (pending) order directly into the
        broker's book -- market orders fill instantly in this mock, so
        order-synchronization tests need a way to present genuinely
        pending broker orders (limit orders resting on the book, orders
        surviving an app restart, etc.)."""
        ticket = str(self._next_ticket)
        self._next_ticket += 1
        self._orders[ticket] = BrokerOpenOrder(
            client_order_id=client_order_id, broker_order_id=ticket, symbol=symbol, side=side,
            order_type=BrokerOrderType.LIMIT, status=BrokerOrderStatus.PENDING,
            requested_price=price or self._tick_price, quantity=quantity, filled_quantity=filled_quantity,
        )
        return ticket
