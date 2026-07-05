"""
Live Execution Engine, per 10_Live_Trading_Engine.md's Order Flow and the
Sprint 4 plan's module 4. Implements exactly:

    Strategy Signal -> Risk Management Engine -> Execution Validation ->
    Broker Adapter -> Broker Confirmation -> Position Update ->
    Portfolio Update -> Audit Log

Central design decision: this does NOT reuse OrderService.create_order()
wholesale (unlike Sprint 3's Paper Trading, which correctly DOES reuse it).
The reason is symmetrical to Sprint 2's Backtesting decision, just for the
opposite reason: OrderService's pricing and "fill" are a SIMULATION
(get_latest_price() + a synthetic slippage constant) — appropriate for
paper trading, wrong for live. Here, the broker's own PlaceOrder() call is
the ONE authoritative source for execution price/quantity/status; nothing
in this engine ever simulates a fill.

What IS reused, deliberately: RiskService (the exact same Risk Engine gate
that governs every simulated and paper order -- "The Risk Engine remains
the single source of truth for trade authorization"), and
apply_fill_to_position (the exact same open/add/close/flip logic Sprint 1
uses), extracted into position_fill_service.py specifically so this engine
and OrderService can share it rather than maintain two implementations of
how a fill affects a position.

Two gates sit BEFORE the Risk Engine, cheapest-first:
1. portfolio.kill_switch_active -- an already-tripped kill switch blocks
   everything, no exceptions, no matter how the order arrived.
2. broker_account.live_trading_enabled -- the explicit "mode switching
   requires user confirmation" gate (10_Live_Trading_Engine.md). Being
   CONNECTED to a broker is not the same as being AUTHORIZED to trade
   through it.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.application.schemas.order import OrderCreateRequest
from app.application.services.position_fill_service import apply_fill_to_position
from app.application.services.risk_service import RiskService
from app.domain.broker.broker_interface import BrokerAdapter
from app.domain.broker.broker_models import (
    BrokerConnectionError,
    BrokerOrderRejectedError,
    BrokerOrderRequest,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerOrderType,
)
from app.domain.risk.risk_models import OrderRequest as DomainOrderRequest
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.models.broker_account import BrokerAccount
from app.infrastructure.models.enums import NotificationSeverity, NotificationType, OrderStatus
from app.infrastructure.models.notification import Notification
from app.infrastructure.models.order import Order
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.repositories.position_repository import PositionRepository

logger = get_logger("live_execution")

_STATUS_MAP = {
    BrokerOrderStatus.FILLED: OrderStatus.FILLED,
    BrokerOrderStatus.PARTIALLY_FILLED: OrderStatus.PARTIALLY_FILLED,
    BrokerOrderStatus.PENDING: OrderStatus.PENDING,
    BrokerOrderStatus.REJECTED: OrderStatus.REJECTED,
    BrokerOrderStatus.CANCELLED: OrderStatus.CANCELLED,
    BrokerOrderStatus.EXPIRED: OrderStatus.CANCELLED,
}
_FILLED_STATUSES = (OrderStatus.FILLED.value, OrderStatus.PARTIALLY_FILLED.value)


class LiveExecutionEngineError(Exception):
    pass


class LiveExecutionEngine:
    def __init__(self, db: Session, broker: BrokerAdapter) -> None:
        self.db = db
        self.broker = broker
        self.risk_service = RiskService(db)
        self.positions = PositionRepository(db)

    def submit_order(
        self, user_id: uuid.UUID, portfolio: Portfolio, broker_account: BrokerAccount, request: OrderCreateRequest,
    ) -> Order:
        if portfolio.kill_switch_active:
            return self._create_rejected_order(
                portfolio, broker_account, request, "Kill switch is active. No new live orders permitted."
            )

        if not broker_account.live_trading_enabled:
            return self._create_rejected_order(
                portfolio, broker_account, request,
                "Live trading is not enabled for this broker account. Explicit confirmation is required first.",
            )

        if not self.broker.is_connected():
            return self._create_rejected_order(portfolio, broker_account, request, "Broker is not connected.")

        reference_price = request.requested_price
        if request.order_type == "market" and reference_price is None:
            # A market order has no price of its own to size against yet --
            # the Risk Engine needs a reference entry price to compute
            # |entry - stop_loss| for position sizing BEFORE the order is
            # ever sent. Pulled from the broker's own tick (the price
            # you're actually about to trade through), not a separate
            # market-data feed that could disagree with it -- correct for
            # live trading in a way OrderService's provider-based lookup
            # (appropriate for its simulated fills) is not.
            try:
                tick = self.broker.get_tick(request.symbol)
            except BrokerConnectionError as exc:
                return self._create_rejected_order(
                    portfolio, broker_account, request, f"Could not fetch a reference price from the broker: {exc}"
                )
            reference_price = tick.ask if request.side == "buy" else tick.bid

        domain_request = DomainOrderRequest(
            symbol=request.symbol, side=request.side, order_type=request.order_type,
            requested_price=reference_price, stop_loss=request.stop_loss,
            take_profit=request.take_profit, quantity=request.quantity, strategy_enabled=True,
        )
        decision = self.risk_service.evaluate(portfolio, user_id, domain_request)
        if not decision.approved:
            return self._create_rejected_order(
                portfolio, broker_account, request, decision.reason or "Rejected by Risk Engine."
            )

        quantity = decision.approved_position_size or request.quantity
        order = Order(
            portfolio_id=portfolio.id, broker_account_id=broker_account.id, strategy_id=request.strategy_id,
            symbol=request.symbol, order_type=request.order_type, side=request.side, quantity=quantity,
            requested_price=request.requested_price, stop_loss=request.stop_loss, take_profit=request.take_profit,
            status=OrderStatus.PENDING.value, submitted_at=dt.datetime.now(dt.timezone.utc),
        )
        self.db.add(order)
        self.db.flush()

        broker_request = BrokerOrderRequest(
            symbol=request.symbol,
            side=BrokerOrderSide.BUY if request.side == "buy" else BrokerOrderSide.SELL,
            order_type=BrokerOrderType(request.order_type),
            quantity=quantity, price=reference_price,
            stop_loss=request.stop_loss, take_profit=request.take_profit,
        )

        try:
            broker_result = self.broker.place_order(broker_request)
        except BrokerOrderRejectedError as exc:
            order.status = OrderStatus.REJECTED.value
            order.rejection_reason = f"Broker rejected: {exc}"
            self._notify(portfolio.user_id, "Live Order Rejected", str(exc), NotificationSeverity.WARNING)
            logger.warning("live_execution.order_rejected_by_broker", extra={"symbol": request.symbol, "reason": str(exc)})
            self.db.commit()
            return order
        except BrokerConnectionError as exc:
            order.status = OrderStatus.REJECTED.value
            order.rejection_reason = f"Broker connection error during submission: {exc}"
            self.db.commit()
            # Deliberately re-raised (not swallowed): the circuit breaker
            # wrapping this engine's broker calls needs to observe this
            # failure to count toward its trip threshold. An order that
            # silently "fails safe" as merely rejected would hide a
            # connectivity problem the operator needs to know about.
            logger.error("live_execution.broker_connection_error", extra={"symbol": request.symbol, "error": str(exc)})
            raise

        order.status = _STATUS_MAP.get(broker_result.status, OrderStatus.REJECTED).value
        order.executed_price = broker_result.executed_price
        order.broker_order_id = broker_result.broker_order_id
        order.filled_at = broker_result.filled_at
        order.rejection_reason = broker_result.reason

        if order.status in _FILLED_STATUSES:
            apply_fill_to_position(self.db, self.positions, portfolio, order)
            self._notify(
                portfolio.user_id, "Live Order Filled",
                f"{request.side} {order.quantity} {request.symbol} @ {order.executed_price} "
                f"(broker order {order.broker_order_id})",
                NotificationSeverity.INFO,
            )
            logger.info(
                "live_execution.order_filled",
                extra={
                    "symbol": request.symbol, "broker_order_id": order.broker_order_id,
                    "executed_price": str(order.executed_price), "quantity": str(order.quantity),
                },
            )
        else:
            self._notify(portfolio.user_id, "Live Order Not Filled", order.rejection_reason or "Unknown broker status.", NotificationSeverity.WARNING)

        self.db.commit()
        return order

    def close_position(
        self, portfolio: Portfolio, broker_account: BrokerAccount, position_id: uuid.UUID, quantity: Decimal | None = None,
    ) -> Order:
        """Closing bypasses Risk Engine evaluation entirely -- same
        principle as OrderService.close_position() and the Backtesting
        Engine's exit handling: risk management exists to gate NEW
        risk-taking, not to prevent reducing existing risk. Does NOT gate
        on broker_account.live_trading_enabled either, for the same
        reason the kill switch itself doesn't block closes -- an operator
        or the emergency kill switch must always be able to get OUT of a
        position even if trading has been otherwise disabled."""
        from app.infrastructure.models.position import Position

        position = self.db.get(Position, position_id)
        if position is None or position.portfolio_id != portfolio.id or position.status != "open":
            raise LiveExecutionEngineError("Open position not found.")
        if not self.broker.is_connected():
            raise LiveExecutionEngineError("Broker is not connected; cannot close position.")

        # The broker is the source of truth for which of ITS tickets this
        # position corresponds to. Looked up by symbol rather than a
        # stored broker_position_id column: the existing system-wide
        # invariant (enforced since Sprint 1's get_open_for_symbol) is one
        # open position per symbol per portfolio, so this is unambiguous
        # today. A hedging-account model with multiple concurrent tickets
        # per symbol would need a stored mapping -- out of scope here.
        broker_positions = self.broker.get_positions(symbol=position.symbol)
        if not broker_positions:
            raise LiveExecutionEngineError(f"No matching broker position found for {position.symbol}.")
        broker_position_id = broker_positions[0].broker_position_id

        close_qty = quantity or position.quantity
        broker_result = self.broker.close_position(broker_position_id, close_qty)

        closing_side = "sell" if position.direction == "long" else "buy"
        order = Order(
            portfolio_id=portfolio.id, broker_account_id=broker_account.id, strategy_id=None,
            symbol=position.symbol, order_type="market", side=closing_side, quantity=close_qty,
            requested_price=None, executed_price=broker_result.executed_price,
            status=_STATUS_MAP.get(broker_result.status, OrderStatus.REJECTED).value,
            broker_order_id=broker_result.broker_order_id, filled_at=broker_result.filled_at,
        )
        self.db.add(order)
        self.db.flush()

        if order.status in _FILLED_STATUSES:
            apply_fill_to_position(self.db, self.positions, portfolio, order)
            self._notify(
                portfolio.user_id, "Live Position Closed",
                f"Closed {close_qty} {position.symbol} @ {broker_result.executed_price}",
                NotificationSeverity.INFO,
            )
            logger.info(
                "live_execution.position_closed",
                extra={"symbol": position.symbol, "broker_order_id": order.broker_order_id},
            )
        self.db.commit()
        return order

    # ------------------------------------------------------------------

    def _create_rejected_order(
        self, portfolio: Portfolio, broker_account: BrokerAccount, request: OrderCreateRequest, reason: str,
    ) -> Order:
        """Every attempted live order is persisted, approved or not --
        same audit-trail principle as OrderService. Rejections at these
        pre-Risk-Engine gates never reach the broker at all."""
        order = Order(
            portfolio_id=portfolio.id, broker_account_id=broker_account.id, strategy_id=request.strategy_id,
            symbol=request.symbol, order_type=request.order_type, side=request.side,
            quantity=request.quantity or Decimal("0"), requested_price=request.requested_price,
            stop_loss=request.stop_loss, take_profit=request.take_profit,
            status=OrderStatus.REJECTED.value, rejection_reason=reason,
            submitted_at=dt.datetime.now(dt.timezone.utc),
        )
        self.db.add(order)
        self._notify(portfolio.user_id, "Live Order Rejected", reason, NotificationSeverity.WARNING)
        logger.warning("live_execution.order_rejected", extra={"symbol": request.symbol, "reason": reason})
        self.db.commit()
        return order

    def _notify(self, user_id: uuid.UUID, title: str, message: str, severity: NotificationSeverity) -> None:
        self.db.add(
            Notification(
                user_id=user_id, type=NotificationType.TRADE.value, title=title, message=message, severity=severity.value,
            )
        )
