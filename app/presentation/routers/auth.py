from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.application.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserResponse,
)
from app.application.schemas.common import MessageResponse
from app.application.services.auth_service import (
    AuthService,
    InactiveUserError,
    InvalidCredentialsError,
    UserAlreadyExistsError,
)
from app.core.dependencies import get_blacklist, get_current_active_user, get_db
from app.infrastructure.models.user import User
from app.infrastructure.security.token_blacklist import TokenBlacklist

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _get_auth_service(db: Session = Depends(get_db), blacklist: TokenBlacklist = Depends(get_blacklist)) -> AuthService:
    return AuthService(db, blacklist)


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, service: AuthService = Depends(_get_auth_service)):
    try:
        user, tokens = service.register(payload.username, payload.email, payload.password)
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RegisterResponse(
        user=UserResponse.model_validate(user), access_token=tokens.access_token, refresh_token=tokens.refresh_token
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, service: AuthService = Depends(_get_auth_service)):
    try:
        _user, tokens = service.login(payload.email, payload.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except InactiveUserError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshRequest, service: AuthService = Depends(_get_auth_service)):
    try:
        new_access_token = service.refresh(payload.refresh_token)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    # A refresh token is reusable until it expires or is explicitly revoked
    # (see /auth/logout) — we don't rotate it on every use for Sprint 1.
    return TokenResponse(access_token=new_access_token, refresh_token=payload.refresh_token)


@router.post("/logout", response_model=MessageResponse)
def logout(payload: RefreshRequest, service: AuthService = Depends(_get_auth_service)):
    service.logout(payload.refresh_token)
    return MessageResponse(detail="Logged out successfully.")


@router.get("/profile", response_model=UserResponse)
def profile(current_user: User = Depends(get_current_active_user)):
    return UserResponse.model_validate(current_user)
