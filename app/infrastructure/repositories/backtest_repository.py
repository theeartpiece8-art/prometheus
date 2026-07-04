import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.backtest import Backtest
from app.infrastructure.models.strategy import Strategy
from app.infrastructure.repositories.base_repository import BaseRepository


class BacktestRepository(BaseRepository[Backtest]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Backtest)

    def list_for_user(self, user_id: uuid.UUID, *, offset: int = 0, limit: int = 100) -> list[Backtest]:
        stmt = (
            select(Backtest)
            .join(Strategy, Backtest.strategy_id == Strategy.id)
            .where(Strategy.user_id == user_id)
            .order_by(Backtest.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_for_user(self, backtest_id: uuid.UUID, user_id: uuid.UUID) -> Backtest | None:
        stmt = (
            select(Backtest)
            .join(Strategy, Backtest.strategy_id == Strategy.id)
            .where(Backtest.id == backtest_id, Strategy.user_id == user_id)
        )
        return self.db.execute(stmt).scalar_one_or_none()
