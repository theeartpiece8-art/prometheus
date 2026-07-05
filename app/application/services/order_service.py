"""
Order service — implements the Order Approval Pipeline from
07_Risk_Management_Engine.md end to end:

    Signal Generated -> Strategy Validation -> Portfolio Risk Check ->
    Exposure Check -> Market Condition Check -> Broker Validation ->
    Risk Engine Approval -> Execution Engine

Sprint 1 scope: no real broker exists yet (10_Live_Trading_Engine.md is a
future sprint), so "Broker Validation" and "Execution Engine" are a
deterministic *simulation* — approved orders are filled immediately at the
resolved market/limit price with a small, clearly-labeled simulated
slippage. No network call to any broker is ever made from this service.

Every order — approved or rejected — is persisted for audit purposes.
Rejections never reach position/portfolio mutation code at all: the
function returns immediately after risk_service.evaluate() reports a
rejection. This is the code-level enforcement of "If the Risk Engine
rejects an order, execution must stop immediately."
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.application.schemas.order import OrderCreateRequest
from app.application.services.risk_service import RiskService
from app.core.decimal_utils import clean_decimal
from app.domain.risk.risk_models import OrderRequest as DomainOrderRequest
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.market_data.provider import get_latest_price
from app.infrastructure.models.enums import (
    NotificationSeverity,
    NotificationType,
    OrderStatus,
    PositionDirection,
    PositionStatus,
    TradeOutcome,
)
from app.infrastructure.models.notification import Notification
from app.infrastructure.models.order import Order
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.position import Position
from app.infrastructure.models.strategy import Strategy
from app.infrastructure.models.trade import Trade
from app.infrastructure.repositories.order_repository import OrderRepository
from app.infrastructure.repositories.portfolio_repository import PortfolioRepository
from app.infrastructure.repositories.position_repository import PositionRepository

logger = get_logger("orders")

# A small, clearly-labeled simulated slippage applied against the trader on
# every simulated fill (e.g. buys fill slightly above quote, sells slightly
# below). This is NOT the configurable slippage model described for the
# Backtesting/Paper Trading Engines (08/09 docs) — those are future-sprint
# work with their own spread/latency modeling. This is just enough realism
# to avoid pretending fills are always at the exact quoted tick.
_SIMULATED_SLIPPAGE_BPS = Decimal("2")  # 0.02%


class OrderServiceError(Exception):
    pass


class StrategyNotFoundError(OrderServiceError):
    pass


class PortfolioNotFoundError(OrderServiceError):
    pass


class OrderService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.orders = OrderRepository(db)
        self.positions = PositionRepository(db)
        self.portfolios = PortfolioRepository(db)
        self.risk_service = RiskService(db)

    def create_order(
        self, user_id: uuid.UUID, portfolio: Portfolio, request: OrderCreateRequest
    ) -> tuple[Order, list, str]:
        """Returns (order, risk_checks, data_source)."""
        # --- Strategy validation ---
        strategy_enabled = True
        if request.strategy_id is not None:
            strategy = self.db.get(Strategy, request.strategy_id)
            if strategy is None or strategy.user_id != user_id:
                raise StrategyNotFoundError("Strategy not found.")
            strategy_enabled = strategy.status == "active"

        # --- Market condition check: resolve a price ---
        data_source = "client_supplied"
        price = request.requested_price
        if request.order_type == "market":
            price, data_source = get_latest_price(request.symbol)

        domain_request = DomainOrderRequest(
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            requested_price=price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            quantity=request.quantity,
            strategy_enabled=strategy_enabled,
        )

        # --- Risk Engine Approval (the one and only gate) ---
        decision = self.risk_service.evaluate(portfolio, user_id, domain_request)

        order = Order(
            portfolio_id=portfolio.id,
            strategy_id=request.strategy_id,
            symbol=request.symbol,
            order_type=request.order_type,
            side=request.side,
            quantity=decision.approved_position_size or request.quantity or Decimal("0"),
            requested_price=price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            submitted_at=dt.datetime.now(dt.timezone.utc),
        )

        if not decision.approved:
            order.status = OrderStatus.REJECTED.value
            order.rejection_reason = decision.reason
            self.db.add(order)
            self._notify(
                user_id, NotificationType.RISK, "Order Rejected",
                f"{request.side.upper()} {request.symbol} was rejected: {decision.reason}",
                NotificationSeverity.WARNING,
            )
            self.db.commit()
            self.db.refresh(order)
            logger.warning("order.rejected", extra={"order_id": str(order.id), "reason": decision.reason})
            return order, decision.checks, data_source

        # --- Execution Engine (simulated fill) ---
        slippage_direction = 1 if request.side == "buy" else -1
        executed_price = price * (1 + slippage_direction * _SIMULATED_SLIPPAGE_BPS / Decimal("10000"))
        order.executed_price = executed_price.quantize(Decimal("0.00000001"))
        order.status = OrderStatus.FILLED.value
        order.filled_at = dt.datetime.now(dt.timezone.utc)
        self.db.add(order)
        self.db.flush()

        self._apply_fill_to_position(portfolio, order)
        self._recompute_portfolio_aggregates(portfolio)

        self._notify(
            user_id, NotificationType.TRADE, "Order Filled",
            f"{request.side.upper()} {order.quantity} {request.symbol} @ {order.executed_price}",
            NotificationSeverity.INFO,
        )

        self.db.commit()
        self.db.refresh(order)
        logger.info(
            "order.filled",
            extra={"order_id": str(order.id), "symbol": order.symbol, "executed_price": str(order.executed_price)},
        )
        return order, decision.checks, data_source

    # ------------------------------------------------------------------
    # Position / portfolio bookkeeping (simulation only — no broker calls)
    # ------------------------------------------------------------------

    def _apply_fill_to_position(self, portfolio: Portfolio, order: Order) -> None:
        from app.application.services.position_fill_service import apply_fill_to_position

        apply_fill_to_position(self.db, self.positions, portfolio, order)

    def _recompute_portfolio_aggregates(self, portfolio: Portfolio) -> None:
        from app.infrastructure.models.equity_history import EquityHistory

        open_positions = self.positions.list_open_for_portfolio(portfolio.id)
        unrealized = Decimal("0")
        for p in open_positions:
            if p.current_price is None:
                continue
            if p.direction == PositionDirection.LONG.value:
                unrealized += (p.current_price - p.average_price) * p.quantity
            else:
                unrealized += (p.average_price - p.current_price) * p.quantity

        portfolio.unrealized_pnl = unrealized
        portfolio.equity = portfolio.balance + unrealized
        portfolio.margin_used = self.positions.total_exposure(portfolio.id)
        portfolio.free_margin = portfolio.equity - portfolio.margin_used
        if portfolio.equity > portfolio.peak_equity:
            portfolio.peak_equity = portfolio.equity

        drawdown = Decimal("0")
        if portfolio.peak_equity > 0:
            drawdown = clean_decimal(max(Decimal("0"), (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity * 100))

        self.db.add(
            EquityHistory(
                portfolio_id=portfolio.id,
                balance=portfolio.balance,
                equity=portfolio.equity,
                drawdown=drawdown,
            )
        )
        self.db.flush()

    def _notify(
        self, user_id: uuid.UUID, ntype: NotificationType, title: str, message: str, severity: NotificationSeverity
    ) -> None:
        self.db.add(
            Notification(
                user_id=user_id, type=ntype.value, title=title, message=message, severity=severity.value
            )
        )

    def close_position(
        self, portfolio: Portfolio, position_id: uuid.UUID, reason: str | None = None,
        strategy_id: uuid.UUID | None = None,
    ) -> Position:
        """`reason`, when provided, customizes the notification title (e.g.
        "Stop Loss Hit" / "Take Profit Hit" for Sprint 3's Paper Trading
        Engine monitoring loop) instead of the generic "Position Closed".
        `strategy_id`, when provided, is stamped on the synthetic closing
        order so the resulting Trade row is attributed to the strategy —
        without it, per-strategy stats (win rate, PnL) can't see closes
        performed by the SL/TP monitor. Both are purely additive; every
        existing caller that omits them is unaffected."""
        position = self.positions.get(position_id)
        if position is None or position.portfolio_id != portfolio.id or position.status != PositionStatus.OPEN.value:
            raise OrderServiceError("Open position not found.")

        # Fetch the owning user for notification purposes (Portfolio -> User is
        # a direct FK, so this is a cheap single-row lookup, not a broad query).
        user_id = portfolio.user_id

        price, _source = get_latest_price(position.symbol)
        closing_side = "sell" if position.direction == PositionDirection.LONG.value else "buy"
        quantity_before_close = position.quantity

        synthetic_order = Order(
            portfolio_id=portfolio.id,
            strategy_id=strategy_id,
            symbol=position.symbol,
            order_type="market",
            side=closing_side,
            quantity=position.quantity,
            requested_price=price,
            executed_price=price,
            status=OrderStatus.FILLED.value,
            submitted_at=dt.datetime.now(dt.timezone.utc),
            filled_at=dt.datetime.now(dt.timezone.utc),
        )
        self.db.add(synthetic_order)
        self.db.flush()

        self._apply_fill_to_position(portfolio, synthetic_order)
        self._recompute_portfolio_aggregates(portfolio)

        self._notify(
            user_id, NotificationType.TRADE, reason or "Position Closed",
            f"Closed {quantity_before_close} {position.symbol} @ {price}. Realized P&L: {position.realized_pnl}",
            NotificationSeverity.INFO,
        )

        self.db.commit()
        self.db.refresh(position)
        logger.info("position.closed", extra={"position_id": str(position_id)})
        return position
