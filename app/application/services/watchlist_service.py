from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.infrastructure.models.watchlist import Watchlist
from app.infrastructure.repositories.watchlist_repository import WatchlistRepository


class WatchlistNotFoundError(Exception):
    pass


class WatchlistService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = WatchlistRepository(db)

    def list_for_user(self, user_id: uuid.UUID) -> list[Watchlist]:
        return self.repo.list_for_user(user_id)

    def create(self, user_id: uuid.UUID, name: str, symbols: list[str]) -> Watchlist:
        watchlist = Watchlist(user_id=user_id, name=name, symbols=symbols)
        self.db.add(watchlist)
        self.db.commit()
        self.db.refresh(watchlist)
        return watchlist

    def get(self, watchlist_id: uuid.UUID, user_id: uuid.UUID) -> Watchlist:
        watchlist = self.repo.get_for_user(watchlist_id, user_id)
        if watchlist is None:
            raise WatchlistNotFoundError("Watchlist not found.")
        return watchlist

    def update(self, watchlist_id: uuid.UUID, user_id: uuid.UUID, **changes) -> Watchlist:
        watchlist = self.get(watchlist_id, user_id)
        for field, value in changes.items():
            if value is not None:
                setattr(watchlist, field, value)
        self.db.commit()
        self.db.refresh(watchlist)
        return watchlist

    def delete(self, watchlist_id: uuid.UUID, user_id: uuid.UUID) -> None:
        watchlist = self.get(watchlist_id, user_id)
        self.db.delete(watchlist)
        self.db.commit()
