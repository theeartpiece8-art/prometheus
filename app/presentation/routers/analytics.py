from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.application.services.analytics_service import AnalyticsService
from app.application.services.portfolio_service import PortfolioService
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.user import User

router = APIRouter(prefix="/analytics", tags=["Analytics"])


class PerformanceResponse(BaseModel):
    total_trades: int
    win_rate: float | None
    expectancy: float | None
    profit_factor: float | None
    average_win: float | None
    average_loss: float | None
    net_profit: float


@router.get("/performance", response_model=PerformanceResponse)
def get_performance(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    portfolio = PortfolioService(db).get_default_for_user(current_user.id)
    summary = AnalyticsService(db).performance_summary(portfolio)
    return PerformanceResponse(
        total_trades=summary["total_trades"],
        win_rate=float(summary["win_rate"]) if summary["win_rate"] is not None else None,
        expectancy=float(summary["expectancy"]) if summary["expectancy"] is not None else None,
        profit_factor=float(summary["profit_factor"]) if summary["profit_factor"] is not None else None,
        average_win=float(summary["average_win"]) if summary["average_win"] is not None else None,
        average_loss=float(summary["average_loss"]) if summary["average_loss"] is not None else None,
        net_profit=float(summary["net_profit"]),
    )


@router.get("/equity")
def get_equity_curve(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    service = PortfolioService(db)
    portfolio = service.get_default_for_user(current_user.id)
    history = service.equity_history(portfolio)
    return [{"timestamp": h.timestamp.isoformat(), "equity": float(h.equity), "balance": float(h.balance)} for h in history]


@router.get("/drawdown")
def get_drawdown_curve(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    service = PortfolioService(db)
    portfolio = service.get_default_for_user(current_user.id)
    history = service.equity_history(portfolio)
    return [{"timestamp": h.timestamp.isoformat(), "drawdown_pct": float(h.drawdown)} for h in history]


@router.get("/strategies")
def compare_strategies(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    """
    Sprint 1 scope: strategy-level performance comparison is meaningful once
    strategies have accumulated real trade history via the (future) Backtesting
    and Paper Trading engines. For now this returns per-strategy trade counts
    from whatever manual/live orders exist, which is honest given the data
    that actually exists yet.
    """
    from decimal import Decimal

    from app.infrastructure.repositories.strategy_repository import StrategyRepository
    from app.infrastructure.repositories.trade_repository import TradeRepository

    strategies = StrategyRepository(db).list_for_user(current_user.id)
    trades_repo = TradeRepository(db)
    results = []
    for s in strategies:
        trades = trades_repo.list_for_strategy(s.id, limit=10_000)
        closed = [t for t in trades if t.net_profit is not None]
        wins = sum(1 for t in closed if t.net_profit > 0)
        net_profit = sum((t.net_profit for t in closed), Decimal("0"))
        results.append(
            {
                "strategy_id": str(s.id), "name": s.name, "status": s.status,
                "total_trades": len(closed),
                "win_rate": round(wins / len(closed) * 100, 2) if closed else None,
                "net_profit": float(net_profit),
            }
        )
    return results
