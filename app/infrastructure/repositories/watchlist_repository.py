import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.watchlist import Watchlist
from app.infrastructure.repositories.base_repository import BaseRepository


class WatchlistRepository(BaseRepository[Watchlist]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Watchlist)

    def list_for_user(self, user_id: uuid.UUID) -> list[Watchlist]:
        return self.list(user_id=user_id, offset=0, limit=100)

    def get_for_user(self, watchlist_id: uuid.UUID, user_id: uuid.UUID) -> Watchlist | None:
        stmt = select(Watchlist).where(Watchlist.id == watchlist_id, Watchlist.user_id == user_id)
        return self.db.execute(stmt).scalar_one_or_none()
