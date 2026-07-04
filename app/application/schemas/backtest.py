import datetime as dt
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class BacktestRunRequest(BaseModel):
    strategy_id: uuid.UUID
    symbol: str = Field(min_length=1, max_length=32)
    timeframe: str = Field(default="1D", description="e.g. 1m, 5m, 15m, 30m, 1H, 4H, 1D, 1W")
    start_date: dt.datetime | None = None
    end_date: dt.datetime | None = None
    initial_balance: Decimal = Field(default=Decimal("10000"), gt=0)
    commission_pct: Decimal = Field(default=Decimal("0"), ge=0, le=10)

    @model_validator(mode="after")
    def _validate_date_range(self) -> "BacktestRunRequest":
        if self.start_date and self.end_date and self.start_date >= self.end_date:
            raise ValueError("start_date must be before end_date")
        return self


class SimulatedTradeResponse(BaseModel):
    symbol: str
    direction: str
    entry_time: dt.datetime
    exit_time: dt.datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    commission: Decimal
    gross_profit: Decimal
    net_profit: Decimal
    close_reason: str
    outcome: str


class EquityPointResponse(BaseModel):
    timestamp: dt.datetime
    equity: Decimal
    drawdown_pct: Decimal


class RiskRejectionResponse(BaseModel):
    timestamp: dt.datetime
    signal_type: str
    reason: str


class BacktestMetricsResponse(BaseModel):
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


class BacktestResultResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    strategy_id: uuid.UUID
    symbol: str
    timeframe: str
    start_date: dt.datetime
    end_date: dt.datetime
    initial_balance: Decimal
    data_source: str = Field(description="'yfinance' or 'mock' — where the historical OHLCV came from")
    bars_processed: int
    metrics: BacktestMetricsResponse
    trades: list[SimulatedTradeResponse]
    equity_curve: list[EquityPointResponse]
    risk_rejections: list[RiskRejectionResponse]
    error_message: str | None = None


class BacktestJobSummaryResponse(BaseModel):
    job_id: uuid.UUID
    strategy_id: uuid.UUID
    symbol: str | None
    timeframe: str | None
    status: str
    win_rate: Decimal | None
    net_profit: Decimal | None
    max_drawdown: Decimal | None
    created_at: dt.datetime


def build_result_response(job) -> BacktestResultResponse:
    """
    Maps a persisted `Backtest` ORM row (dedicated columns + `results` JSON
    blob) to the API response shape. This is pure data reshaping — no
    decisions, no DB access, no business rules — kept out of the router
    per Sprint 2's explicit "no logic in controllers" requirement, and out
    of BacktestService because it's a presentation concern (HTTP response
    shape), not a backtest execution concern.
    """
    results = job.results or {}
    metrics = BacktestMetricsResponse(
        total_trades=len(results.get("trades", [])),
        win_rate=job.win_rate,
        total_pnl=job.net_profit or Decimal("0"),
        gross_profit=Decimal(results["gross_profit"]) if results.get("gross_profit") is not None else Decimal("0"),
        gross_loss=Decimal(results["gross_loss"]) if results.get("gross_loss") is not None else Decimal("0"),
        profit_factor=job.profit_factor,
        expectancy=job.expectancy,
        average_win=Decimal(results["average_win"]) if results.get("average_win") is not None else None,
        average_loss=Decimal(results["average_loss"]) if results.get("average_loss") is not None else None,
        largest_win=Decimal(results["largest_win"]) if results.get("largest_win") is not None else None,
        largest_loss=Decimal(results["largest_loss"]) if results.get("largest_loss") is not None else None,
        max_drawdown_pct=job.max_drawdown or Decimal("0"),
        sharpe_ratio=job.sharpe_ratio,
        sortino_ratio=job.sortino_ratio,
        consecutive_wins=results.get("consecutive_wins", 0),
        consecutive_losses=results.get("consecutive_losses", 0),
        final_balance=job.ending_balance or job.initial_balance or Decimal("0"),
        final_equity=job.ending_balance or job.initial_balance or Decimal("0"),
    )
    return BacktestResultResponse(
        job_id=job.id,
        status=job.status,
        strategy_id=job.strategy_id,
        symbol=job.symbol or "",
        timeframe=job.timeframe or "",
        start_date=job.start_date,
        end_date=job.end_date,
        initial_balance=job.initial_balance or Decimal("0"),
        data_source=results.get("data_source", "unknown"),
        bars_processed=results.get("bars_processed", 0),
        metrics=metrics,
        trades=[SimulatedTradeResponse(**t) for t in results.get("trades", [])],
        equity_curve=[EquityPointResponse(**p) for p in results.get("equity_curve", [])],
        risk_rejections=[RiskRejectionResponse(**r) for r in results.get("risk_rejections", [])],
        error_message=job.error_message,
    )


def build_summary_response(job) -> BacktestJobSummaryResponse:
    return BacktestJobSummaryResponse(
        job_id=job.id, strategy_id=job.strategy_id, symbol=job.symbol, timeframe=job.timeframe,
        status=job.status, win_rate=job.win_rate, net_profit=job.net_profit,
        max_drawdown=job.max_drawdown, created_at=job.created_at,
    )
