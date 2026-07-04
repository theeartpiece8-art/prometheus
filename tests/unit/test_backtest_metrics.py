import datetime as dt
from decimal import Decimal

from app.domain.backtesting.backtest_models import EquityPoint, SimulatedTrade, TradeCloseReason
from app.domain.backtesting.metrics import compute_metrics, max_drawdown_pct, sharpe_ratio, sortino_ratio

START = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)


def _curve(equities: list[float]) -> list[EquityPoint]:
    points = []
    peak = equities[0]
    for i, e in enumerate(equities):
        peak = max(peak, e)
        dd = (peak - e) / peak * 100 if peak > 0 else 0
        points.append(EquityPoint(timestamp=START + dt.timedelta(days=i), equity=Decimal(str(e)), drawdown_pct=Decimal(str(dd))))
    return points


def _trade(net_profit: float, outcome: str | None = None) -> SimulatedTrade:
    o = outcome or ("win" if net_profit > 0 else "loss" if net_profit < 0 else "breakeven")
    return SimulatedTrade(
        symbol="AAPL", direction="long", entry_time=START, exit_time=START + dt.timedelta(days=1),
        entry_price=Decimal("100"), exit_price=Decimal("100") + Decimal(str(net_profit)), quantity=Decimal("1"),
        commission=Decimal("0"), gross_profit=Decimal(str(net_profit)), net_profit=Decimal(str(net_profit)),
        close_reason=TradeCloseReason.SIGNAL_REVERSAL, outcome=o,
    )


class TestMaxDrawdown:
    def test_no_drawdown_on_monotonic_uptrend(self):
        curve = _curve([100, 105, 110, 120, 130])
        assert max_drawdown_pct(curve) == Decimal("0")

    def test_captures_intra_run_trough_not_just_final_value(self):
        # Ends flat/positive, but dips hard in the middle — max_drawdown
        # must reflect the WORST point, not the final one.
        curve = _curve([100, 150, 75, 140])  # 50% peak-to-trough dip in the middle
        dd = max_drawdown_pct(curve)
        assert dd == Decimal("50")

    def test_empty_curve_returns_zero(self):
        assert max_drawdown_pct([]) == Decimal("0")

    def test_recovers_and_new_drawdown_uses_new_peak(self):
        curve = _curve([100, 200, 190, 300, 270])  # second dip is 10% off the new 300 peak
        dd = max_drawdown_pct(curve)
        assert dd == Decimal("10")


class TestSharpeAndSortino:
    def test_flat_equity_curve_returns_none_for_both(self):
        curve = _curve([100, 100, 100, 100, 100])
        assert sharpe_ratio(curve, "1D") is None
        assert sortino_ratio(curve, "1D") is None

    def test_short_curve_returns_none(self):
        curve = _curve([100, 101])
        assert sharpe_ratio(curve, "1D") is None
        assert sortino_ratio(curve, "1D") is None

    def test_positive_trend_gives_positive_ratios(self):
        curve = _curve([100, 102, 101, 104, 103, 106, 108])
        sharpe = sharpe_ratio(curve, "1D")
        sortino = sortino_ratio(curve, "1D")
        assert sharpe is not None and sharpe > 0
        assert sortino is not None and sortino > 0

    def test_sortino_ignores_upside_volatility_sharpe_does_not(self):
        """Two curves with the same mean return: one with big up-swings and
        tiny down-moves, one with symmetric swings. Sortino should treat
        the asymmetric (mostly-upside) curve much more favorably relative
        to Sharpe, since Sharpe penalizes ALL volatility including the
        upside swings."""
        asymmetric = _curve([100, 130, 128, 160, 158, 190])  # big up, tiny down
        sharpe_asym = sharpe_ratio(asymmetric, "1D")
        sortino_asym = sortino_ratio(asymmetric, "1D")

        assert sharpe_asym is not None
        assert sortino_asym is not None
        # Sortino divides by downside-only deviation (small here), Sharpe by
        # total deviation (larger, since it includes the big up-moves) — so
        # Sortino should come out substantially larger for this curve shape.
        assert sortino_asym > sharpe_asym

    def test_pure_downtrend_gives_negative_ratios(self):
        curve = _curve([100, 98, 95, 90, 85])
        sharpe = sharpe_ratio(curve, "1D")
        sortino = sortino_ratio(curve, "1D")
        assert sharpe is not None and sharpe < 0
        assert sortino is not None and sortino < 0


class TestComputeMetrics:
    def test_no_trades_returns_safe_defaults_no_crash(self):
        curve = _curve([100, 100, 100])
        m = compute_metrics([], curve, final_balance=Decimal("100"), final_equity=Decimal("100"), timeframe="1D")
        assert m.total_trades == 0
        assert m.win_rate is None
        assert m.profit_factor is None
        assert m.total_pnl == Decimal("0")

    def test_all_winning_trades(self):
        trades = [_trade(10), _trade(20), _trade(15)]
        curve = _curve([100, 110, 130, 145])
        m = compute_metrics(trades, curve, final_balance=Decimal("145"), final_equity=Decimal("145"), timeframe="1D")
        assert m.total_trades == 3
        assert m.win_rate == Decimal("100")
        assert m.gross_loss == Decimal("0")
        assert m.profit_factor is None  # undefined when there are zero losses (would divide by zero)
        assert m.total_pnl == Decimal("45")

    def test_mixed_wins_and_losses(self):
        trades = [_trade(100), _trade(-50), _trade(80), _trade(-30)]
        curve = _curve([1000, 1100, 1050, 1130, 1100])
        m = compute_metrics(trades, curve, final_balance=Decimal("1100"), final_equity=Decimal("1100"), timeframe="1D")
        assert m.total_trades == 4
        assert m.win_rate == Decimal("50")
        assert m.gross_profit == Decimal("180")
        assert m.gross_loss == Decimal("80")
        assert m.profit_factor == Decimal("180") / Decimal("80")
        assert m.largest_win == Decimal("100")
        assert m.largest_loss == Decimal("-50")

    def test_consecutive_wins_and_losses_tracked_correctly(self):
        trades = [_trade(10), _trade(10), _trade(-5), _trade(-5), _trade(-5), _trade(10)]
        curve = _curve([100] * 7)
        m = compute_metrics(trades, curve, final_balance=Decimal("115"), final_equity=Decimal("115"), timeframe="1D")
        assert m.consecutive_wins == 2
        assert m.consecutive_losses == 3

    def test_breakeven_trade_counts_as_neither_win_nor_loss(self):
        trades = [_trade(0)]
        curve = _curve([100, 100])
        m = compute_metrics(trades, curve, final_balance=Decimal("100"), final_equity=Decimal("100"), timeframe="1D")
        assert m.win_rate == Decimal("0")  # not in `wins` (requires > 0)
        assert m.gross_loss == Decimal("0")  # not in `losses` either (requires < 0)
