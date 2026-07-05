"""
Shared position-update logic, extracted from OrderService so both
simulated fills (Sprint 1 OrderService, Sprint 3 PaperTradingService via
OrderService) and REAL broker fills (Sprint 4 LiveExecutionEngine) apply
the identical open/add/close/flip rules to a position. Before this
extraction, LiveExecutionEngine would have needed its own copy of this
logic -- a real risk of the two drifting apart over time on something
this consequential (this is the code that turns a fill into realized
P&L). Takes a fully-populated Order (already committed with its
executed_price/quantity/side/strategy_id/id set, regardless of whether
that came from a simulated fill or a real broker confirmation) and a
PositionRepository, so it's agnostic to the fill's origin.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.infrastructure.models.enums import PositionDirection, PositionStatus, TradeOutcome
from app.infrastructure.models.order import Order
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.position import Position
from app.infrastructure.models.trade import Trade
from app.infrastructure.repositories.position_repository import PositionRepository


def apply_fill_to_position(db, positions: PositionRepository, portfolio: Portfolio, order: Order) -> None:
    existing = positions.get_open_for_symbol(portfolio.id, order.symbol)
    incoming_direction = PositionDirection.LONG if order.side == "buy" else PositionDirection.SHORT

    if existing is None:
        position = Position(
            portfolio_id=portfolio.id,
            symbol=order.symbol,
            direction=incoming_direction.value,
            quantity=order.quantity,
            average_price=order.executed_price,
            current_price=order.executed_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            opened_at=dt.datetime.now(dt.timezone.utc),
            status=PositionStatus.OPEN.value,
        )
        db.add(position)
        db.flush()
        return

    if existing.direction == incoming_direction.value:
        # Adding to the position: weighted-average the entry price.
        total_qty = existing.quantity + order.quantity
        existing.average_price = (
            (existing.average_price * existing.quantity) + (order.executed_price * order.quantity)
        ) / total_qty
        existing.quantity = total_qty
        existing.current_price = order.executed_price
        # A fresh stop_loss/take_profit on this fill replaces the
        # position's existing levels (the newer signal's risk
        # parameters take precedence); omitted values leave the
        # position's current levels untouched.
        if order.stop_loss is not None:
            existing.stop_loss = order.stop_loss
        if order.take_profit is not None:
            existing.take_profit = order.take_profit
        db.flush()
        return

    # Opposite direction: this fill reduces, closes, or flips the position.
    closing_qty = min(existing.quantity, order.quantity)
    if existing.direction == PositionDirection.LONG.value:
        realized = (order.executed_price - existing.average_price) * closing_qty
    else:
        realized = (existing.average_price - order.executed_price) * closing_qty

    commission = Decimal("0")  # No commission model configured yet (same as Sprint 1's simulated path).
    trade = Trade(
        strategy_id=order.strategy_id,
        order_id=order.id,
        position_id=existing.id,
        symbol=order.symbol,
        entry_price=existing.average_price,
        exit_price=order.executed_price,
        quantity=closing_qty,
        gross_profit=realized,
        net_profit=realized - commission,
        commission=commission,
        outcome=(
            TradeOutcome.WIN.value if realized > 0
            else TradeOutcome.LOSS.value if realized < 0
            else TradeOutcome.BREAKEVEN.value
        ),
    )
    db.add(trade)

    existing.realized_pnl += realized
    existing.quantity -= closing_qty
    portfolio.balance += realized

    if existing.quantity == 0:
        existing.status = PositionStatus.CLOSED.value
        existing.closed_at = dt.datetime.now(dt.timezone.utc)
    db.flush()

    remainder = order.quantity - closing_qty
    if remainder > 0:
        # Flip: the fill fully closed the old position; open a new one
        # in the opposite direction with whatever quantity is left over.
        flipped = Position(
            portfolio_id=portfolio.id,
            symbol=order.symbol,
            direction=incoming_direction.value,
            quantity=remainder,
            average_price=order.executed_price,
            current_price=order.executed_price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            opened_at=dt.datetime.now(dt.timezone.utc),
            status=PositionStatus.OPEN.value,
        )
        db.add(flipped)
        db.flush()
