import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.strategy import Strategy
from app.infrastructure.repositories.base_repository import BaseRepository


class StrategyRepository(BaseRepository[Strategy]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Strategy)

    def list_for_user(self, user_id: uuid.UUID, *, offset: int = 0, limit: int = 100) -> list[Strategy]:
        return self.list(user_id=user_id, offset=offset, limit=limit)

    def get_for_user(self, strategy_id: uuid.UUID, user_id: uuid.UUID) -> Strategy | None:
        stmt = select(Strategy).where(Strategy.id == strategy_id, Strategy.user_id == user_id)
        return self.db.execute(stmt).scalar_one_or_none()
