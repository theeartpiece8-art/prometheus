import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.repositories.base_repository import BaseRepository


class PortfolioRepository(BaseRepository[Portfolio]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Portfolio)

    def list_for_user(self, user_id: uuid.UUID) -> list[Portfolio]:
        return self.list(user_id=user_id, offset=0, limit=100)

    def get_default_for_user(self, user_id: uuid.UUID) -> Portfolio | None:
        """Sprint 1 simplification: each user has exactly one portfolio,
        created automatically at registration (see AuthService)."""
        stmt = select(Portfolio).where(Portfolio.user_id == user_id).order_by(Portfolio.created_at.asc())
        return self.db.execute(stmt).scalars().first()

    def get_for_user(self, portfolio_id: uuid.UUID, user_id: uuid.UUID) -> Portfolio | None:
        stmt = select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
        return self.db.execute(stmt).scalar_one_or_none()
