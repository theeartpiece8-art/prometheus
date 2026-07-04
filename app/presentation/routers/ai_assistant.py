"""
AI Research Assistant API.

11_AI_Research_Assistant.md requires the assistant to be strictly
analytical/read-only, to never place or influence trades, and to avoid
"absolute predictions." Sprint 1 implements this as a genuine — if
simple — deterministic, rule-based analyzer over real Strategy/Trade data
(no LLM call, no external API, no network access, nothing to hallucinate).
This is honest, working functionality rather than a stub: given a
strategy with trade history, it always returns a real structured
explanation built directly from that strategy's numbers.

Explicitly enforced here, at the router boundary: this module imports
nothing from app.application.services.order_service or any execution
path. There is no code path from this router to the Risk Engine, Order
Service, or any broker/execution concern — satisfying "No write access to
execution systems" structurally, not just by convention.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.application.services.analytics_service import AnalyticsService
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.strategy import Strategy
from app.infrastructure.models.user import User
from app.infrastructure.repositories.trade_repository import TradeRepository

router = APIRouter(prefix="/ai", tags=["AI Research Assistant (read-only)"])


class AnalyzeRequest(BaseModel):
    strategy_id: uuid.UUID


class CompareRequest(BaseModel):
    strategy_ids: list[uuid.UUID]


class AnalysisResponse(BaseModel):
    summary: str
    observations: list[str]
    metrics_interpretation: list[str]
    risks_identified: list[str]
    suggestions: list[str]


def _get_owned_strategy(db: Session, strategy_id: uuid.UUID, user_id: uuid.UUID) -> Strategy:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None or strategy.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found.")
    return strategy


def _analyze_strategy(db: Session, strategy: Strategy) -> AnalysisResponse:
    trades = TradeRepository(db).list_for_strategy(strategy.id, limit=10_000)
    closed = [t for t in trades if t.net_profit is not None]

    if not closed:
        return AnalysisResponse(
            summary=f"'{strategy.name}' has no completed trades yet, so no performance analysis is possible.",
            observations=["No trade history is available for this strategy."],
            metrics_interpretation=[],
            risks_identified=[
                "Without trade history, this strategy is unvalidated. Per 09_Paper_Trading_Engine.md, "
                "a strategy should demonstrate stable behavior in paper trading before being considered "
                "for live trading."
            ],
            suggestions=[
                "Run this strategy against historical or paper-traded data to accumulate trades before "
                "drawing conclusions.",
            ],
        )

    from decimal import Decimal

    wins = [t for t in closed if t.net_profit > 0]
    losses = [t for t in closed if t.net_profit < 0]
    win_rate = len(wins) / len(closed) * 100
    net_profit = sum((t.net_profit for t in closed), Decimal("0"))

    observations = [
        f"{len(closed)} completed trade(s) recorded.",
        f"Win rate: {win_rate:.1f}% ({len(wins)} wins / {len(losses)} losses).",
        f"Net profit across recorded trades: {net_profit}.",
    ]

    metrics_interpretation = []
    if win_rate < 40:
        metrics_interpretation.append(
            "Win rate is below 40%. This is not necessarily a problem on its own — a low win rate can "
            "still be profitable if average wins substantially exceed average losses — but it does mean "
            "position sizing and stop discipline matter more for this strategy."
        )
    elif win_rate > 70:
        metrics_interpretation.append(
            "Win rate is unusually high. Worth checking whether this reflects a genuinely robust edge or "
            "a small sample size / favorable market regime that may not persist — high win rates on few "
            "trades are a classic overfitting warning sign (see 11_AI_Research_Assistant.md's Overfitting "
            "Detection capability)."
        )

    risks_identified = []
    if len(closed) < 30:
        risks_identified.append(
            f"Only {len(closed)} trade(s) recorded — too small a sample to draw statistically reliable "
            "conclusions about this strategy's true edge."
        )

    suggestions = [
        "Compare this strategy's out-of-sample performance once the Backtesting Engine's walk-forward "
        "testing is available, per 08_Backtesting_Engine.md.",
    ]

    return AnalysisResponse(
        summary=f"'{strategy.name}' has {len(closed)} completed trade(s) with a {win_rate:.1f}% win rate "
        f"and {net_profit} net profit so far.",
        observations=observations,
        metrics_interpretation=metrics_interpretation or ["No notable metric anomalies detected."],
        risks_identified=risks_identified or ["No elevated risks detected from trade history alone."],
        suggestions=suggestions,
    )


@router.post("/analyze", response_model=AnalysisResponse)
def analyze(payload: AnalyzeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    strategy = _get_owned_strategy(db, payload.strategy_id, current_user.id)
    return _analyze_strategy(db, strategy)


@router.post("/compare", response_model=dict[str, AnalysisResponse])
def compare(payload: CompareRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    result = {}
    for sid in payload.strategy_ids:
        strategy = _get_owned_strategy(db, sid, current_user.id)
        result[str(sid)] = _analyze_strategy(db, strategy)
    return result


@router.post("/report", response_model=AnalysisResponse)
def generate_report(payload: AnalyzeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """Sprint 1: identical content to /analyze, returned in the same structured
    format described by 11_AI_Research_Assistant.md's 'Output Format' section.
    A distinct persisted/downloadable report artifact is future work (see reports.py)."""
    strategy = _get_owned_strategy(db, payload.strategy_id, current_user.id)
    return _analyze_strategy(db, strategy)
