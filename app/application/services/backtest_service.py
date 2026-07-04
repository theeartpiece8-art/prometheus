"""
Backtest service: the Application-layer bridge between the pure Domain
BacktestEngine and the database/market-data infrastructure. Mirrors the
role RiskService plays for live orders (see risk_service.py) — this is
the ONLY place that constructs BacktestConfig/loads bars/instantiates
strategies and calls BacktestEngine.run().

Execution is synchronous within the request per Sprint 2 scope (see
README "Sprint 2 scope notes"). `MAX_BACKTEST_BARS` guards against a
pathological request (e.g. 1-minute bars over 5 years) hanging a worker
indefinitely; background job execution is flagged as future work rather
than silently allowing unbounded requests.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.application.schemas.backtest import BacktestRunRequest
from app.application.services.risk_service import RiskService
from app.domain.backtesting.backtest_engine import BacktestEngine
from app.domain.backtesting.backtest_models import BacktestConfig, BacktestRunResult
from app.domain.risk.risk_engine import risk_engine
from app.domain.strategy.base_strategy import Bar
from app.domain.strategy.sample_strategies import instantiate_strategy
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.market_data.base_provider import MarketDataProvider, MarketDataProviderError
from app.infrastructure.market_data.provider import default_provider
from app.infrastructure.market_data.utils import fetch_bars_with_source
from app.infrastructure.models.backtest import Backtest
from app.infrastructure.models.strategy import Strategy
from app.infrastructure.repositories.backtest_repository import BacktestRepository
from app.infrastructure.repositories.strategy_repository import StrategyRepository

logger = get_logger("backtest")

MAX_BACKTEST_BARS = 5000
DEFAULT_LOOKBACK_DAYS = 365


class BacktestServiceError(Exception):
    pass


class StrategyNotFoundError(BacktestServiceError):
    pass


class InsufficientDataError(BacktestServiceError):
    pass


class BacktestJobNotFoundError(BacktestServiceError):
    pass


class BacktestService:
    def __init__(self, db: Session, market_data_provider: MarketDataProvider | None = None) -> None:
        self.db = db
        self.strategies = StrategyRepository(db)
        self.backtests = BacktestRepository(db)
        self.risk_service = RiskService(db)
        # Defaults to the same yfinance-with-mock-fallback provider used
        # everywhere else in the app; injectable for tests or for a future
        # caller that wants a specific data source (e.g. re-run against
        # mock data only, bypassing live fetch, without touching global config).
        self._provider = market_data_provider or default_provider

    def run_backtest(self, user_id: uuid.UUID, request: BacktestRunRequest) -> Backtest:
        strategy = self.strategies.get_for_user(request.strategy_id, user_id)
        if strategy is None:
            raise StrategyNotFoundError("Strategy not found.")

        end_date = request.end_date or dt.datetime.now(dt.timezone.utc)
        start_date = request.start_date or (end_date - dt.timedelta(days=DEFAULT_LOOKBACK_DAYS))

        job = Backtest(
            strategy_id=strategy.id, symbol=request.symbol, timeframe=request.timeframe,
            start_date=start_date, end_date=end_date, initial_balance=request.initial_balance,
            status="running",
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)

        try:
            result = self._execute(user_id, strategy, request, start_date, end_date)
        except BacktestServiceError as exc:
            job.status = "failed"
            job.error_message = str(exc)
            self.db.commit()
            self.db.refresh(job)
            logger.warning("backtest.failed", extra={"job_id": str(job.id), "reason": str(exc)})
            return job
        except Exception as exc:  # noqa: BLE001 — never let an unexpected error leave a job stuck "running"
            job.status = "failed"
            job.error_message = f"Unexpected error: {exc}"
            self.db.commit()
            self.db.refresh(job)
            logger.exception("backtest.unexpected_failure", extra={"job_id": str(job.id)})
            return job

        self._persist_result(job, result)
        logger.info(
            "backtest.completed",
            extra={
                "job_id": str(job.id), "strategy_id": str(strategy.id), "symbol": request.symbol,
                "trades": len(result.trades), "net_profit": str(result.metrics.total_pnl),
                "data_source": result.data_source,
            },
        )
        return job

    def get_for_user(self, job_id: uuid.UUID, user_id: uuid.UUID) -> Backtest:
        job = self.backtests.get_for_user(job_id, user_id)
        if job is None:
            raise BacktestJobNotFoundError("Backtest job not found.")
        return job

    def list_for_user(self, user_id: uuid.UUID, offset: int = 0, limit: int = 100) -> list[Backtest]:
        return self.backtests.list_for_user(user_id, offset=offset, limit=limit)

    # ------------------------------------------------------------------

    def _execute(
        self, user_id: uuid.UUID, strategy: Strategy, request: BacktestRunRequest,
        start_date: dt.datetime, end_date: dt.datetime,
    ) -> BacktestRunResult:
        try:
            strategy_instance, strategy_type = instantiate_strategy(strategy.parameters)
        except ValueError as exc:
            raise BacktestServiceError(f"{exc} on strategy {strategy.id}.") from exc

        strategy_parameters = strategy_instance.parameters  # already stripped of STRATEGY_TYPE_KEY
        validation_errors = strategy_instance.validate_parameters()
        if validation_errors:
            raise BacktestServiceError(f"Strategy parameters are invalid: {'; '.join(validation_errors)}")

        try:
            raw_bars, data_source = fetch_bars_with_source(
                self._provider, request.symbol, request.timeframe, start_date, end_date
            )
        except MarketDataProviderError as exc:
            raise InsufficientDataError(f"Could not load historical data for {request.symbol}: {exc}") from exc

        if not raw_bars:
            raise InsufficientDataError(
                f"No historical data available for {request.symbol} between {start_date.date()} and {end_date.date()}."
            )
        if len(raw_bars) > MAX_BACKTEST_BARS:
            raise InsufficientDataError(
                f"Requested range would produce {len(raw_bars)} bars, exceeding the Sprint 2 synchronous-execution "
                f"limit of {MAX_BACKTEST_BARS}. Use a shorter date range or a coarser timeframe."
            )

        bars = [_to_domain_bar(b) for b in raw_bars]

        risk_settings = self.risk_service.load_risk_settings(user_id)
        config = BacktestConfig(
            symbol=request.symbol, timeframe=request.timeframe, start_date=start_date, end_date=end_date,
            initial_balance=request.initial_balance, strategy_type=strategy_type,
            strategy_parameters=strategy_parameters, commission_pct=request.commission_pct,
        )
        engine = BacktestEngine(strategy_instance, risk_engine, risk_settings, config)
        return engine.run(bars, data_source=data_source)

    def _persist_result(self, job: Backtest, result: BacktestRunResult) -> None:
        m = result.metrics
        job.status = "completed"
        job.ending_balance = m.final_balance
        job.net_profit = m.total_pnl
        job.win_rate = m.win_rate
        job.expectancy = m.expectancy
        job.profit_factor = m.profit_factor
        job.sharpe_ratio = m.sharpe_ratio
        job.sortino_ratio = m.sortino_ratio
        job.max_drawdown = m.max_drawdown_pct
        job.results = {
            "data_source": result.data_source,
            "bars_processed": result.bars_processed,
            "commission_pct": str(result.config.commission_pct),
            "gross_profit": str(m.gross_profit),
            "gross_loss": str(m.gross_loss),
            "average_win": str(m.average_win) if m.average_win is not None else None,
            "average_loss": str(m.average_loss) if m.average_loss is not None else None,
            "largest_win": str(m.largest_win) if m.largest_win is not None else None,
            "largest_loss": str(m.largest_loss) if m.largest_loss is not None else None,
            "consecutive_wins": m.consecutive_wins,
            "consecutive_losses": m.consecutive_losses,
            "trades": [_trade_to_dict(t) for t in result.trades],
            "equity_curve": [_equity_point_to_dict(p) for p in result.equity_curve],
            "risk_rejections": [_rejection_to_dict(r) for r in result.risk_rejections],
        }
        self.db.commit()
        self.db.refresh(job)


def _to_domain_bar(raw: dict) -> Bar:
    return Bar(
        timestamp=raw["timestamp"],
        open=Decimal(str(raw["open"])),
        high=Decimal(str(raw["high"])),
        low=Decimal(str(raw["low"])),
        close=Decimal(str(raw["close"])),
        volume=Decimal(str(raw["volume"])),
    )


def _trade_to_dict(t) -> dict:
    return {
        "symbol": t.symbol, "direction": t.direction,
        "entry_time": t.entry_time.isoformat(), "exit_time": t.exit_time.isoformat(),
        "entry_price": str(t.entry_price), "exit_price": str(t.exit_price), "quantity": str(t.quantity),
        "commission": str(t.commission), "gross_profit": str(t.gross_profit), "net_profit": str(t.net_profit),
        "close_reason": t.close_reason.value, "outcome": t.outcome,
    }


def _equity_point_to_dict(p) -> dict:
    return {"timestamp": p.timestamp.isoformat(), "equity": str(p.equity), "drawdown_pct": str(p.drawdown_pct)}


def _rejection_to_dict(r) -> dict:
    return {"timestamp": r.timestamp.isoformat(), "signal_type": r.signal_type.value, "reason": r.reason}
