import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.order import Order
from app.infrastructure.repositories.base_repository import BaseRepository


class OrderRepository(BaseRepository[Order]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Order)

    def list_for_portfolio(self, portfolio_id: uuid.UUID, *, offset: int = 0, limit: int = 100) -> list[Order]:
        stmt = (
            select(Order)
            .where(Order.portfolio_id == portfolio_id)
            .order_by(Order.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def list_open_for_portfolio(self, portfolio_id: uuid.UUID) -> list[Order]:
        stmt = select(Order).where(
            Order.portfolio_id == portfolio_id, Order.status.in_(["pending", "approved", "partially_filled"])
        )
        return list(self.db.execute(stmt).scalars().all())
