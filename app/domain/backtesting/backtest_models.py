"""
Domain-level data structures for the Backtesting Engine, per
08_Backtesting_Engine.md. Plain dataclasses, no SQLAlchemy/FastAPI/network
dependency — same pattern as app/domain/risk/risk_models.py. The
Application layer (backtest_service.py) translates ORM rows and yfinance
data into these structures and back.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from app.domain.strategy.base_strategy import SignalType


@dataclass(frozen=True)
class BacktestConfig:
    symbol: str
    timeframe: str
    start_date: dt.datetime
    end_date: dt.datetime
    initial_balance: Decimal
    strategy_type: str
    strategy_parameters: dict
    commission_pct: Decimal = Decimal("0")
    """Percentage commission charged on both entry and exit notional, e.g.
    Decimal('0.05') = 0.05%. Defaults to zero (spec: commission model is
    user-configurable; zero is a valid, explicit configuration)."""


class TradeCloseReason(str, Enum):
    SIGNAL_REVERSAL = "signal_reversal"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    END_OF_BACKTEST = "end_of_backtest"


@dataclass(frozen=True)
class SimulatedTrade:
    symbol: str
    direction: str  # "long" | "short"
    entry_time: dt.datetime
    exit_time: dt.datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    commission: Decimal
    gross_profit: Decimal
    net_profit: Decimal
    close_reason: TradeCloseReason
    outcome: str  # "win" | "loss" | "breakeven"


@dataclass(frozen=True)
class EquityPoint:
    timestamp: dt.datetime
    equity: Decimal
    drawdown_pct: Decimal


@dataclass(frozen=True)
class RiskRejection:
    """A signal the strategy generated that the Risk Engine refused to
    approve. Tracked explicitly so a backtest result can prove the Risk
    Engine was actually in the loop, not just nominally present."""
    timestamp: dt.datetime
    signal_type: SignalType
    reason: str


@dataclass(frozen=True)
class BacktestMetrics:
    total_trades: int
    win_rate: Decimal | None
    total_pnl: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal | None
    average_win: Decimal | None
    average_loss: Decimal | None
    largest_win: Decimal | None
    largest_loss: Decimal | None
    max_drawdown_pct: Decimal
    sharpe_ratio: Decimal | None
    sortino_ratio: Decimal | None
    consecutive_wins: int
    consecutive_losses: int
    final_balance: Decimal
    final_equity: Decimal


@dataclass(frozen=True)
class BacktestRunResult:
    config: BacktestConfig
    trades: list[SimulatedTrade]
    equity_curve: list[EquityPoint]
    risk_rejections: list[RiskRejection]
    metrics: BacktestMetrics
    bars_processed: int
    data_source: str  # "yfinance" | "mock"
