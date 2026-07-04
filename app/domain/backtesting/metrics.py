"""
Pure metric calculations over a completed list of SimulatedTrade /
EquityPoint objects, per 08_Backtesting_Engine.md's "Performance Metrics"
section and Sprint 2's requirements (win rate, net/gross profit/loss,
Sharpe, Sortino, profit factor, max drawdown, expectancy). No I/O, fully
unit-testable.
"""
from __future__ import annotations

import math
from decimal import Decimal

from app.domain.backtesting.backtest_models import BacktestMetrics, EquityPoint, SimulatedTrade

ZERO = Decimal("0")

_ANNUALIZATION_FACTOR = {
    "1m": 252 * 6.5 * 60, "5m": 252 * 6.5 * 12, "15m": 252 * 6.5 * 4, "30m": 252 * 6.5 * 2,
    "1H": 252 * 6.5, "4H": 252 * 6.5 / 4, "1D": 252, "daily": 252, "1W": 52, "weekly": 52,
}


def max_drawdown_pct(equity_curve: list[EquityPoint]) -> Decimal:
    """Worst peak-to-trough decline observed at ANY point in the run — not
    just the drawdown at the final bar. This is the standard definition of
    Maximum Drawdown and is computed as a running max, matching how
    Portfolio.peak_equity is tracked for live trading (risk_service.py)."""
    if not equity_curve:
        return ZERO
    peak = equity_curve[0].equity
    worst = ZERO
    for point in equity_curve:
        if point.equity > peak:
            peak = point.equity
        if peak > 0:
            dd = (peak - point.equity) / peak * 100
            if dd > worst:
                worst = dd
    return worst


def _bar_returns(equity_curve: list[EquityPoint]) -> list[float]:
    """Bar-over-bar percentage returns of the equity curve, as plain
    floats (statistics on Decimal is needlessly slow/awkward; precision
    loss here is immaterial — these feed a ratio, not money accounting)."""
    returns: list[float] = []
    for prev, curr in zip(equity_curve, equity_curve[1:]):
        if prev.equity > 0:
            returns.append(float((curr.equity - prev.equity) / prev.equity))
    return returns


def sharpe_ratio(equity_curve: list[EquityPoint], timeframe: str) -> Decimal | None:
    """Simplified Sharpe ratio (risk-free rate assumed 0, a common
    simplification for strategy-comparison purposes): mean return over
    the standard deviation of ALL returns (up and down movement both
    count against it), annualized by timeframe."""
    if len(equity_curve) < 3:
        return None
    returns = _bar_returns(equity_curve)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    stdev = math.sqrt(variance)
    if stdev == 0:
        return None
    annualization = _ANNUALIZATION_FACTOR.get(timeframe, 252)
    value = (mean / stdev) * math.sqrt(annualization)
    return Decimal(str(round(value, 4)))


def sortino_ratio(equity_curve: list[EquityPoint], timeframe: str) -> Decimal | None:
    """Like Sharpe, but only penalizes DOWNSIDE volatility — up-moves don't
    count against the strategy. Downside deviation uses the standard
    convention: sqrt(mean(min(0, r)^2)) over ALL returns (not just the
    negative ones), so a strategy with few-but-large losses among many
    flat/positive bars is still correctly penalized relative to its total
    time in the market. Returns None if there is no downside variance at
    all (undefined / infinite ratio), mirroring how sharpe_ratio handles
    zero variance."""
    if len(equity_curve) < 3:
        return None
    returns = _bar_returns(equity_curve)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    downside_variance = sum(min(0.0, r) ** 2 for r in returns) / len(returns)
    downside_deviation = math.sqrt(downside_variance)
    if downside_deviation == 0:
        return None
    annualization = _ANNUALIZATION_FACTOR.get(timeframe, 252)
    value = (mean / downside_deviation) * math.sqrt(annualization)
    return Decimal(str(round(value, 4)))


def compute_metrics(
    trades: list[SimulatedTrade],
    equity_curve: list[EquityPoint],
    final_balance: Decimal,
    final_equity: Decimal,
    timeframe: str,
) -> BacktestMetrics:
    drawdown = max_drawdown_pct(equity_curve)
    sharpe = sharpe_ratio(equity_curve, timeframe)
    sortino = sortino_ratio(equity_curve, timeframe)

    if not trades:
        return BacktestMetrics(
            total_trades=0, win_rate=None, total_pnl=ZERO, gross_profit=ZERO, gross_loss=ZERO,
            profit_factor=None, expectancy=None, average_win=None, average_loss=None,
            largest_win=None, largest_loss=None, max_drawdown_pct=drawdown,
            sharpe_ratio=sharpe, sortino_ratio=sortino,
            consecutive_wins=0, consecutive_losses=0, final_balance=final_balance, final_equity=final_equity,
        )

    wins = [t for t in trades if t.net_profit > 0]
    losses = [t for t in trades if t.net_profit < 0]

    gross_profit = sum((t.net_profit for t in wins), ZERO)
    gross_loss = abs(sum((t.net_profit for t in losses), ZERO))
    total_pnl = sum((t.net_profit for t in trades), ZERO)

    win_rate = Decimal(len(wins)) / Decimal(len(trades)) * 100
    average_win = (gross_profit / len(wins)) if wins else None
    average_loss = (gross_loss / len(losses)) if losses else None
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
    expectancy = (
        (win_rate / 100 * (average_win or ZERO)) - ((1 - win_rate / 100) * (average_loss or ZERO))
    )
    largest_win = max((t.net_profit for t in wins), default=None)
    largest_loss = min((t.net_profit for t in losses), default=None)

    consecutive_wins = _max_consecutive(trades, lambda t: t.net_profit > 0)
    consecutive_losses = _max_consecutive(trades, lambda t: t.net_profit < 0)

    return BacktestMetrics(
        total_trades=len(trades),
        win_rate=win_rate,
        total_pnl=total_pnl,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        expectancy=expectancy,
        average_win=average_win,
        average_loss=average_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        max_drawdown_pct=drawdown,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        consecutive_wins=consecutive_wins,
        consecutive_losses=consecutive_losses,
        final_balance=final_balance,
        final_equity=final_equity,
    )


def _max_consecutive(trades: list[SimulatedTrade], predicate) -> int:
    best = current = 0
    for t in trades:
        if predicate(t):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best
