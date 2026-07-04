"""
Paper Trading API — Sprint 3: the Paper Trading Engine is now fully
implemented (09_Paper_Trading_Engine.md). Automated, continuously-running
strategy sessions execute against live market data through the SAME
OrderService/RiskService pipeline manual orders use — see
app/application/services/paper_trading_service.py for the design writeup.

This router contains NO business logic (Sprint coding standard: "no logic
in controllers"): each handler is parse -> service call -> response-schema
construction. Response shaping lives in
app/application/schemas/paper_trading.py.

Endpoint mapping vs 05_API_Specification.md:
- POST /paper/start, POST /paper/stop, GET /paper/status, GET /paper/trades
  are the originally-documented four.
- POST /paper/pause, /paper/resume, /paper/reset, GET /paper/sessions,
  GET /paper/sessions/{id}/monitor and POST /paper/sessions/{id}/tick are
  additions required by 09_Paper_Trading_Engine.md's Session Management /
  Strategy Monitoring sections (the API spec doc predates that level of
  detail — the engine spec wins, same precedence call as Sprint 1's
  risk_settings decision).
- /stop, /pause, /resume act on an explicit session_id (multi-session
  support means "stop" must say WHICH session).
"""
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.application.schemas.paper_trading import (
    PaperTradeResponse,
    SessionResponse,
    StartSessionRequest,
    StrategyMonitorResponse,
    TickResultResponse,
    build_session_response,
    build_trade_response,
)
from app.application.services.paper_trading_service import (
    InvalidSessionStateError,
    PaperTradingService,
    ResetNotAllowedError,
    SessionNotFoundError,
    SessionValidationError,
)
from app.application.services.portfolio_service import PortfolioService
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.user import User
from app.infrastructure.repositories.trade_repository import TradeRepository

router = APIRouter(prefix="/paper", tags=["Paper Trading"])


class SessionActionRequest(BaseModel):
    session_id: uuid.UUID


class ResetAccountRequest(BaseModel):
    starting_balance: Decimal = Field(default=Decimal("10000"), gt=0)


class PaperStatusResponse(BaseModel):
    running_sessions: int
    paused_sessions: int
    sessions: list[SessionResponse]


@router.post("/start", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def start_paper_trading(
    payload: StartSessionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """Validates per the spec's startup rules (enabled strategy, supported
    symbols, live data, initialized portfolio, valid risk settings) and
    rejects with 422 — creating NO session — if any check fails."""
    try:
        session = PaperTradingService(db).start_session(current_user.id, payload)
    except SessionValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    return build_session_response(session)


@router.post("/stop", response_model=SessionResponse)
def stop_paper_trading(
    payload: SessionActionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    try:
        session = PaperTradingService(db).stop_session(payload.session_id, current_user.id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidSessionStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return build_session_response(session)


@router.post("/pause", response_model=SessionResponse)
def pause_paper_trading(
    payload: SessionActionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    try:
        session = PaperTradingService(db).pause_session(payload.session_id, current_user.id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidSessionStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return build_session_response(session)


@router.post("/resume", response_model=SessionResponse)
def resume_paper_trading(
    payload: SessionActionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    try:
        session = PaperTradingService(db).resume_session(payload.session_id, current_user.id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidSessionStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return build_session_response(session)


@router.post("/reset")
def reset_paper_account(
    payload: ResetAccountRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """Resets the paper portfolio to a fresh starting balance. Refused
    (409) while any session is running/paused or any position is open —
    see PaperTradingService.reset_paper_account's docstring."""
    try:
        portfolio = PaperTradingService(db).reset_paper_account(current_user.id, payload.starting_balance)
    except ResetNotAllowedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"balance": portfolio.balance, "equity": portfolio.equity, "message": "Paper account reset."}


@router.get("/status", response_model=PaperStatusResponse)
def paper_trading_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    sessions = PaperTradingService(db).list_for_user(current_user.id)
    return PaperStatusResponse(
        running_sessions=sum(1 for s in sessions if s.status == "running"),
        paused_sessions=sum(1 for s in sessions if s.status == "paused"),
        sessions=[build_session_response(s) for s in sessions],
    )


@router.get("/sessions", response_model=list[SessionResponse])
def list_sessions(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Full session history — 'Session history must remain available after
    shutdown' (spec, Session Management)."""
    return [build_session_response(s) for s in PaperTradingService(db).list_for_user(current_user.id)]


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    try:
        session = PaperTradingService(db).get_for_user(session_id, current_user.id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return build_session_response(session)


@router.get("/sessions/{session_id}/monitor", response_model=list[StrategyMonitorResponse])
def session_strategy_monitor(
    session_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """Per-strategy live stats (spec 'Strategy Monitoring' section)."""
    try:
        monitors = PaperTradingService(db).strategy_monitor(session_id, current_user.id)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [StrategyMonitorResponse(**m) for m in monitors]


@router.post("/sessions/{session_id}/tick", response_model=TickResultResponse)
def run_tick_now(
    session_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """Runs one evaluation tick immediately, without waiting for the
    background scheduler's interval. Useful for manual verification (and
    exactly what the automated tests drive). Only the owning user may
    tick a session; a non-running session is a safe no-op."""
    service = PaperTradingService(db)
    try:
        service.get_for_user(session_id, current_user.id)  # ownership check
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return service.run_tick(session_id)


@router.get("/trades", response_model=list[PaperTradeResponse])
def paper_trades(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """All closed paper trades for the user's portfolio — includes trades
    from automated sessions AND manual orders (they share one portfolio
    and one Risk Engine by design)."""
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    trades = TradeRepository(db).list_for_portfolio(portfolio.id)
    return [build_trade_response(t) for t in trades]
