"""
Analytics service.

Sprint 1 scope: computes real metrics from whatever Trade/Order data
actually exists for the user (there won't be much yet, since the
Backtesting Engine itself is future work) rather than faking numbers.
With zero trades, every metric reports as zero/None rather than raising —
the UI should treat that as "no data yet", not an error.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.trade import Trade


class AnalyticsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _trades_for_portfolio(self, portfolio: Portfolio) -> list[Trade]:
        # Trade -> Order -> Portfolio (Trade always has order_id; see trade.py)
        from app.infrastructure.models.order import Order

        stmt = select(Trade).join(Order, Trade.order_id == Order.id).where(Order.portfolio_id == portfolio.id)
        return list(self.db.execute(stmt).scalars().all())

    def performance_summary(self, portfolio: Portfolio) -> dict:
        trades = self._trades_for_portfolio(portfolio)
        closed = [t for t in trades if t.net_profit is not None]

        if not closed:
            return {
                "total_trades": 0, "win_rate": None, "expectancy": None,
                "profit_factor": None, "average_win": None, "average_loss": None,
                "net_profit": Decimal("0"),
            }

        wins = [t for t in closed if t.net_profit > 0]
        losses = [t for t in closed if t.net_profit < 0]
        gross_profit = sum((t.net_profit for t in wins), Decimal("0"))
        gross_loss = abs(sum((t.net_profit for t in losses), Decimal("0")))
        net_profit = sum((t.net_profit for t in closed), Decimal("0"))
        win_rate = Decimal(len(wins)) / Decimal(len(closed)) * 100
        avg_win = (gross_profit / len(wins)) if wins else Decimal("0")
        avg_loss = (gross_loss / len(losses)) if losses else Decimal("0")
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

        return {
            "total_trades": len(closed),
            "win_rate": win_rate,
            "expectancy": expectancy,
            "profit_factor": profit_factor,
            "average_win": avg_win,
            "average_loss": avg_loss,
            "net_profit": net_profit,
        }
