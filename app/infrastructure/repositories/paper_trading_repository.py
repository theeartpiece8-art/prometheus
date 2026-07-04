import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.paper_trading_session import PaperTradingSession
from app.infrastructure.repositories.base_repository import BaseRepository


class PaperTradingSessionRepository(BaseRepository[PaperTradingSession]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, PaperTradingSession)

    def list_for_user(self, user_id: uuid.UUID, *, offset: int = 0, limit: int = 100) -> list[PaperTradingSession]:
        stmt = (
            select(PaperTradingSession)
            .where(PaperTradingSession.user_id == user_id)
            .order_by(PaperTradingSession.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def get_for_user(self, session_id: uuid.UUID, user_id: uuid.UUID) -> PaperTradingSession | None:
        stmt = select(PaperTradingSession).where(
            PaperTradingSession.id == session_id, PaperTradingSession.user_id == user_id
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def list_running(self) -> list[PaperTradingSession]:
        """Used at app startup to detect sessions left 'running' by an
        unclean shutdown — see main.py's lifespan handler."""
        stmt = select(PaperTradingSession).where(PaperTradingSession.status == "running")
        return list(self.db.execute(stmt).scalars().all())
