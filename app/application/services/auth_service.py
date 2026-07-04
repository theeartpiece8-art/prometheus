"""
Authentication service.

Handles registration, login, token refresh, and logout. On registration,
also provisions a default Portfolio and UserSettings row for the new user
(Sprint 1 simplification: one portfolio per user — see PortfolioRepository).
"""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config import get_settings
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.models.enums import UserRole
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.settings import UserSettings
from app.infrastructure.models.user import User
from app.infrastructure.repositories.user_repository import UserRepository
from app.infrastructure.security.jwt import (
    DecodedToken,
    TokenError,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.infrastructure.security.password import hash_password, verify_password
from app.infrastructure.security.token_blacklist import TokenBlacklist

logger = get_logger("auth")
settings = get_settings()


class AuthError(Exception):
    """Base class for authentication failures. Routers translate these to HTTP 401/409."""


class InvalidCredentialsError(AuthError):
    pass


class UserAlreadyExistsError(AuthError):
    pass


class InactiveUserError(AuthError):
    pass


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str


class AuthService:
    def __init__(self, db: Session, blacklist: TokenBlacklist) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.blacklist = blacklist

    def register(self, username: str, email: str, password: str) -> tuple[User, TokenPair]:
        email = email.lower()
        if self.users.email_taken(email):
            raise UserAlreadyExistsError("An account with this email already exists.")
        if self.users.username_taken(username):
            raise UserAlreadyExistsError("This username is already taken.")

        user = User(
            username=username,
            email=email,
            password_hash=hash_password(password),
            role=UserRole.TRADER.value,
            is_active=True,
        )
        self.db.add(user)
        self.db.flush()

        # Provision defaults so the user can place orders / see settings immediately.
        portfolio = Portfolio(
            user_id=user.id,
            name="Default Portfolio",
            balance=Decimal(str(settings.DEFAULT_STARTING_BALANCE)),
            equity=Decimal(str(settings.DEFAULT_STARTING_BALANCE)),
            free_margin=Decimal(str(settings.DEFAULT_STARTING_BALANCE)),
            peak_equity=Decimal(str(settings.DEFAULT_STARTING_BALANCE)),
        )
        user_settings = UserSettings(user_id=user.id)
        self.db.add_all([portfolio, user_settings])
        self.db.commit()
        self.db.refresh(user)

        logger.info("user.registered", extra={"user_id": str(user.id), "username": username})
        tokens = self._issue_token_pair(user)
        return user, tokens

    def login(self, email: str, password: str) -> tuple[User, TokenPair]:
        user = self.users.get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            logger.warning("auth.login_failed", extra={"email": email})
            raise InvalidCredentialsError("Incorrect email or password.")
        if not user.is_active:
            raise InactiveUserError("This account has been deactivated.")

        user.last_login = dt.datetime.now(dt.timezone.utc)
        self.db.commit()
        self.db.refresh(user)

        logger.info("auth.login_success", extra={"user_id": str(user.id)})
        tokens = self._issue_token_pair(user)
        return user, tokens

    def refresh(self, refresh_token: str) -> str:
        try:
            decoded: DecodedToken = decode_token(refresh_token, expected_type=TokenType.REFRESH)
        except TokenError as exc:
            raise InvalidCredentialsError(str(exc)) from exc

        if self.blacklist.is_revoked(decoded.jti):
            raise InvalidCredentialsError("This refresh token has been revoked.")

        user = self.users.get(decoded.user_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("User no longer exists or is inactive.")

        return create_access_token(user.id, user.role)

    def logout(self, refresh_token: str) -> None:
        """Invalidate the current session by revoking its refresh token."""
        try:
            decoded = decode_token(refresh_token, expected_type=TokenType.REFRESH)
        except TokenError:
            return  # already invalid/expired — logout is idempotent
        ttl = max(1, int((decoded.exp - dt.datetime.now(dt.timezone.utc)).total_seconds()))
        self.blacklist.revoke(decoded.jti, ttl)
        logger.info("auth.logout", extra={"user_id": str(decoded.user_id)})

    def _issue_token_pair(self, user: User) -> TokenPair:
        access = create_access_token(user.id, user.role)
        refresh, _jti = create_refresh_token(user.id, user.role)
        return TokenPair(access_token=access, refresh_token=refresh)
