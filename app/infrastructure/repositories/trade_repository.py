import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.position import Position
from app.infrastructure.models.trade import Trade
from app.infrastructure.repositories.base_repository import BaseRepository


class TradeRepository(BaseRepository[Trade]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Trade)

    def list_for_strategy(self, strategy_id: uuid.UUID, *, offset: int = 0, limit: int = 100) -> list[Trade]:
        return self.list(strategy_id=strategy_id, offset=offset, limit=limit)

    def list_for_portfolio(self, portfolio_id: uuid.UUID, *, offset: int = 0, limit: int = 200) -> list[Trade]:
        """All closed trades for a portfolio, newest first. Trade has no
        direct portfolio_id column (04_Database_Design.md), so this joins
        through Position — the same linkage realized_loss_since already
        relies on. Sprint 3: backs GET /api/v1/paper/trades."""
        stmt = (
            select(Trade)
            .join(Position, Trade.position_id == Position.id)
            .where(Position.portfolio_id == portfolio_id)
            .order_by(Trade.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def realized_loss_since(self, portfolio_id: uuid.UUID, since: dt.datetime) -> Decimal:
        """
        Sum of realized losses (as a positive number) on trades closed for
        this portfolio since `since`. Used by the Risk Engine's daily-loss
        check — "Monitor realized and unrealized losses" per
        07_Risk_Management_Engine.md.
        """
        stmt = (
            select(Trade)
            .join(Position, Trade.position_id == Position.id)
            .where(Position.portfolio_id == portfolio_id, Trade.created_at >= since)
        )
        trades = self.db.execute(stmt).scalars().all()
        loss = Decimal("0")
        for t in trades:
            if t.net_profit is not None and t.net_profit < 0:
                loss += abs(t.net_profit)
        return loss
