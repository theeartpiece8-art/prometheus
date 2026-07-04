from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models.user import User
from app.infrastructure.repositories.base_repository import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, User)

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == email.lower())
        return self.db.execute(stmt).scalar_one_or_none()

    def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username)
        return self.db.execute(stmt).scalar_one_or_none()

    def email_taken(self, email: str) -> bool:
        return self.get_by_email(email) is not None

    def username_taken(self, username: str) -> bool:
        return self.get_by_username(username) is not None
