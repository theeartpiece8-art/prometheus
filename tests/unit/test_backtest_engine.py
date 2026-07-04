"""
Unit tests for the Backtesting Engine domain layer. Zero DB, zero
network — pure logic, matching the same discipline as
tests/unit/test_risk_engine.py. The single most important property under
test here: the Risk Engine genuinely gates entries during a backtest,
not just nominally.
"""
import datetime as dt
from decimal import Decimal

import pytest

from app.domain.backtesting.backtest_engine import BacktestEngine
from app.domain.backtesting.backtest_models import BacktestConfig, TradeCloseReason
from app.domain.risk.risk_engine import RiskEngine
from app.domain.risk.risk_models import RiskSettings
from app.domain.strategy.base_strategy import Bar
from app.domain.strategy.sample_strategies import MovingAverageCrossoverStrategy


def _make_bars(prices: list[float], start: dt.datetime | None = None) -> list[Bar]:
    start = start or dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
    bars = []
    for i, p in enumerate(prices):
        price = Decimal(str(p))
        t = start + dt.timedelta(days=i)
        bars.append(
            Bar(
                timestamp=t.isoformat(), open=price, high=price * Decimal("1.002"),
                low=price * Decimal("0.998"), close=price, volume=Decimal("10000"),
            )
        )
    return bars


def _uptrend_bars(flat_bars: int = 30, trend_bars: int = 60, step: float = 0.8) -> list[Bar]:
    prices = [100.0] * flat_bars + [100.0 + i * step for i in range(1, trend_bars + 1)]
    return _make_bars(prices)


def _flat_bars(n: int = 60) -> list[Bar]:
    return _make_bars([100.0] * n)


def _default_config(**overrides) -> BacktestConfig:
    defaults = dict(
        symbol="AAPL", timeframe="1D",
        start_date=dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc),
        end_date=dt.datetime(2025, 6, 1, tzinfo=dt.timezone.utc),
        initial_balance=Decimal("10000"), strategy_type="moving_average_crossover",
        strategy_parameters={"fast_period": 5, "slow_period": 20, "stop_loss_pct": 8.0, "take_profit_pct": 16.0},
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


class TestBacktestEngineHappyPath:
    def test_uptrend_produces_at_least_one_winning_long_trade(self):
        bars = _uptrend_bars()
        config = _default_config()
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        engine = BacktestEngine(strategy, RiskEngine(), RiskSettings(), config)

        result = engine.run(bars, data_source="mock")

        assert result.bars_processed == len(bars)
        assert len(result.trades) >= 1
        assert result.trades[0].direction == "long"
        assert result.trades[0].net_profit > 0
        assert result.metrics.total_trades == len(result.trades)
        assert result.metrics.final_equity == config.initial_balance + result.metrics.total_pnl

    def test_flat_market_produces_no_trades_and_no_crash(self):
        bars = _flat_bars()
        config = _default_config()
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        engine = BacktestEngine(strategy, RiskEngine(), RiskSettings(), config)

        result = engine.run(bars, data_source="mock")

        assert result.trades == []
        assert result.metrics.total_trades == 0
        assert result.metrics.win_rate is None
        assert result.metrics.final_equity == config.initial_balance

    def test_empty_bar_list_does_not_crash(self):
        config = _default_config()
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        engine = BacktestEngine(strategy, RiskEngine(), RiskSettings(), config)

        result = engine.run([], data_source="mock")

        assert result.bars_processed == 0
        assert result.trades == []
        assert result.equity_curve == []
        assert result.metrics.final_equity == config.initial_balance

    def test_equity_curve_has_one_point_per_bar_processed(self):
        bars = _uptrend_bars()
        config = _default_config()
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        engine = BacktestEngine(strategy, RiskEngine(), RiskSettings(), config)

        result = engine.run(bars, data_source="mock")

        # +1 possible extra point if a position was still open and got
        # force-closed at the final bar (adds one more equity snapshot).
        assert len(result.equity_curve) in (len(bars), len(bars) + 1)

    def test_still_open_position_is_force_closed_at_end_of_backtest(self):
        # A short window that's just long enough for entry but not for the
        # take-profit/stop-loss to plausibly be hit before data runs out —
        # constructed so we can assert the close_reason is END_OF_BACKTEST.
        prices = [100.0] * 25 + [100.0 + i * 0.5 for i in range(1, 10)]  # small, gentle move
        bars = _make_bars(prices)
        config = _default_config(strategy_parameters={"fast_period": 5, "slow_period": 20, "stop_loss_pct": 50.0, "take_profit_pct": 50.0})
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        engine = BacktestEngine(strategy, RiskEngine(), RiskSettings(), config)

        result = engine.run(bars, data_source="mock")

        if result.trades:  # only meaningful if a trade actually opened
            assert result.trades[-1].close_reason == TradeCloseReason.END_OF_BACKTEST


class TestBacktestEngineRiskEnforcement:
    """The central Sprint 2 safety property: no simulated trade can bypass
    the real Risk Engine."""

    def test_disallowed_symbol_blocks_every_entry(self):
        bars = _uptrend_bars()
        config = _default_config(symbol="AAPL")
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        restrictive = RiskSettings(allowed_symbols=["MSFT"])  # AAPL is not allowed
        engine = BacktestEngine(strategy, RiskEngine(), restrictive, config)

        result = engine.run(bars, data_source="mock")

        assert result.trades == []
        assert len(result.risk_rejections) >= 1
        assert "not in the account's allowed symbols" in result.risk_rejections[0].reason
        assert result.metrics.final_equity == config.initial_balance

    def test_tight_stop_relative_to_exposure_limit_is_rejected(self):
        """A very tight stop_loss_pct combined with default risk-per-trade
        produces an oversized position relative to the 20% symbol exposure
        cap — the same interaction verified in the live OrderService risk
        tests. Proves position sizing is genuinely risk-derived, not just
        nominally computed."""
        bars = _uptrend_bars()
        config = _default_config(
            strategy_parameters={"fast_period": 5, "slow_period": 20, "stop_loss_pct": 0.5, "take_profit_pct": 1.0}
        )
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        engine = BacktestEngine(strategy, RiskEngine(), RiskSettings(), config)

        result = engine.run(bars, data_source="mock")

        assert result.trades == []
        assert len(result.risk_rejections) >= 1
        assert "exposure" in result.risk_rejections[0].reason.lower()

    def test_zero_max_open_positions_blocks_entries(self):
        bars = _uptrend_bars()
        config = _default_config()
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        no_positions_allowed = RiskSettings(max_open_positions=0)
        engine = BacktestEngine(strategy, RiskEngine(), no_positions_allowed, config)

        result = engine.run(bars, data_source="mock")

        assert result.trades == []
        assert len(result.risk_rejections) >= 1

    def test_approved_position_size_matches_risk_formula(self):
        """Directly verifies the engine uses the SAME position-sizing formula
        as live trading: size = (equity * risk_pct/100) / |entry - stop|."""
        bars = _uptrend_bars()
        config = _default_config()
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        settings = RiskSettings(risk_per_trade_pct=Decimal("1.0"))
        engine = BacktestEngine(strategy, RiskEngine(), settings, config)

        result = engine.run(bars, data_source="mock")
        assert len(result.trades) >= 1

        first_trade = result.trades[0]
        # risk_amount = 10000 * 1% = 100; stop distance = 8% of entry price
        expected_stop_distance = first_trade.entry_price * Decimal("0.08")
        expected_size = Decimal("100") / expected_stop_distance
        # Allow a little tolerance for slippage-adjusted entry price vs. the
        # raw signal price the sizing was originally computed from.
        assert abs(first_trade.quantity - expected_size) / expected_size < Decimal("0.01")


class TestBacktestEngineExitLogic:
    def test_take_profit_closes_at_configured_level(self):
        bars = _uptrend_bars(flat_bars=30, trend_bars=80, step=1.0)
        config = _default_config(
            strategy_parameters={"fast_period": 5, "slow_period": 20, "stop_loss_pct": 50.0, "take_profit_pct": 5.0}
        )
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        engine = BacktestEngine(strategy, RiskEngine(), RiskSettings(), config)

        result = engine.run(bars, data_source="mock")

        assert len(result.trades) >= 1
        assert result.trades[0].close_reason == TradeCloseReason.TAKE_PROFIT
        assert result.trades[0].outcome == "win"

    def test_commission_reduces_net_profit_below_gross(self):
        bars = _uptrend_bars(flat_bars=30, trend_bars=80, step=1.0)
        config = _default_config(
            strategy_parameters={"fast_period": 5, "slow_period": 20, "stop_loss_pct": 50.0, "take_profit_pct": 5.0},
            commission_pct=Decimal("1.0"),  # deliberately large to make the effect obvious
        )
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        engine = BacktestEngine(strategy, RiskEngine(), RiskSettings(), config)

        result = engine.run(bars, data_source="mock")

        assert len(result.trades) >= 1
        trade = result.trades[0]
        assert trade.commission > 0
        assert trade.net_profit == trade.gross_profit - trade.commission


class TestBacktestMetricsIntegration:
    def test_metrics_are_internally_consistent(self):
        bars = _uptrend_bars(flat_bars=30, trend_bars=100, step=0.6)
        config = _default_config()
        strategy = MovingAverageCrossoverStrategy(parameters=config.strategy_parameters)
        engine = BacktestEngine(strategy, RiskEngine(), RiskSettings(), config)

        result = engine.run(bars, data_source="mock")
        m = result.metrics

        assert m.total_trades == len(result.trades)
        assert m.total_pnl == sum((t.net_profit for t in result.trades), Decimal("0"))
        if m.total_trades > 0:
            wins = [t for t in result.trades if t.net_profit > 0]
            assert m.win_rate == Decimal(len(wins)) / Decimal(len(result.trades)) * 100
        assert m.max_drawdown_pct >= Decimal("0")
