import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.risk_event import RiskEvent
from app.infrastructure.repositories.base_repository import BaseRepository


class RiskEventRepository(BaseRepository[RiskEvent]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, RiskEvent)

    def list_for_portfolio(self, portfolio_id: uuid.UUID, *, offset: int = 0, limit: int = 100) -> list[RiskEvent]:
        stmt = (
            select(RiskEvent)
            .where(RiskEvent.portfolio_id == portfolio_id)
            .order_by(RiskEvent.timestamp.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())
