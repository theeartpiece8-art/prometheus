"""
FastAPI dependency providers: DB session, current user resolution, and
role-based access control. Every protected router depends on
`get_current_user` (or `require_role(...)`) rather than reading the
Authorization header itself — this is the single choke point for auth,
per 12_Coding_Standards.md's "No business logic in controllers".
"""
from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.infrastructure.database.session import get_db
from app.infrastructure.models.enums import UserRole
from app.infrastructure.models.user import User
from app.infrastructure.security.jwt import TokenError, TokenType, decode_token
from app.infrastructure.security.token_blacklist import TokenBlacklist, get_token_blacklist

_bearer_scheme = HTTPBearer(auto_error=True, description="Access token issued by /api/v1/auth/login")

__all__ = ["get_db", "get_current_user", "get_current_active_user", "require_role", "get_blacklist"]


def get_blacklist() -> TokenBlacklist:
    return get_token_blacklist()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
    blacklist: TokenBlacklist = Depends(get_blacklist),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        decoded = decode_token(credentials.credentials, expected_type=TokenType.ACCESS)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc), headers={"WWW-Authenticate": "Bearer"}
        ) from exc

    if blacklist.is_revoked(decoded.jti):
        raise unauthorized

    user = db.get(User, decoded.user_id)
    if user is None:
        raise unauthorized
    return user


def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account has been deactivated.")
    return user


def require_role(*allowed_roles: UserRole):
    """Usage: `Depends(require_role(UserRole.ADMIN))`."""

    def _dependency(user: User = Depends(get_current_active_user)) -> User:
        if user.role not in {r.value for r in allowed_roles}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of the following roles: {[r.value for r in allowed_roles]}.",
            )
        return user

    return _dependency
