"""
JWT access/refresh token issuance and validation.

Per 03_System_Architecture.md / 12_Coding_Standards.md: JWT auth, role-based
access control, refresh tokens.
"""
import datetime as dt
import uuid
from dataclasses import dataclass
from enum import Enum

import jwt

from app.config import get_settings

settings = get_settings()


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


class TokenError(Exception):
    """Raised for any invalid, expired, or malformed token."""


@dataclass
class DecodedToken:
    user_id: uuid.UUID
    role: str
    token_type: TokenType
    jti: str
    exp: dt.datetime


def _create_token(user_id: uuid.UUID, role: str, token_type: TokenType, expires_delta: dt.timedelta) -> tuple[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    jti = str(uuid.uuid4())
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
        "jti": jti,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, jti


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    token, _ = _create_token(
        user_id, role, TokenType.ACCESS, dt.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return token


def create_refresh_token(user_id: uuid.UUID, role: str) -> tuple[str, str]:
    """Returns (token, jti) — the jti is needed by callers that track/revoke refresh tokens."""
    return _create_token(
        user_id, role, TokenType.REFRESH, dt.timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )


def decode_token(token: str, expected_type: TokenType | None = None) -> DecodedToken:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired.") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("Token is invalid.") from exc

    try:
        token_type = TokenType(payload["type"])
        decoded = DecodedToken(
            user_id=uuid.UUID(payload["sub"]),
            role=payload["role"],
            token_type=token_type,
            jti=payload["jti"],
            exp=dt.datetime.fromtimestamp(payload["exp"], tz=dt.timezone.utc),
        )
    except (KeyError, ValueError) as exc:
        raise TokenError("Token payload is malformed.") from exc

    if expected_type is not None and decoded.token_type != expected_type:
        raise TokenError(f"Expected a {expected_type.value} token, got {decoded.token_type.value}.")

    return decoded
