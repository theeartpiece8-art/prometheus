"""
The Backtesting Engine, per 08_Backtesting_Engine.md.

Critical integration property (Sprint 2 requirement: "Uses existing Order +
Risk Engine... Every simulated trade must pass Risk Engine validation"):
every single entry decision in this file is routed through
`app.domain.risk.risk_engine.RiskEngine.evaluate_order` — the EXACT SAME
pure function that gates live and paper orders in `OrderService`. This is
not a parallel re-implementation of risk logic; it is literally the same
call, fed a bar-by-bar simulated AccountState instead of a database-derived
one.

What this deliberately does NOT do: call `OrderService.create_order()`
directly. That method prices market orders off the *current live* quote
(`get_latest_price`) and writes to the user's real Portfolio/Order/
Notification tables — both wrong for a historical replay, which needs the
price *at that point in history* and must never mutate real paper-trading
state. Reusing the Risk Engine's decision logic while keeping execution
simulation isolated is the architecturally correct form of "reuse" here;
see the README's Sprint 2 section for the full reasoning.

Exits (stop-loss, take-profit, signal reversal, end-of-backtest) do NOT
route through the Risk Engine — this mirrors Sprint 1's OrderService,
where `close_position()` also bypasses the risk gate. Risk management
exists to gate *new* risk-taking, not to prevent reducing existing risk.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from app.domain.backtesting.backtest_models import (
    BacktestConfig,
    BacktestRunResult,
    EquityPoint,
    RiskRejection,
    SimulatedTrade,
    TradeCloseReason,
)
from app.domain.backtesting.metrics import compute_metrics
from app.domain.risk.risk_engine import RiskEngine
from app.domain.risk.risk_models import AccountState, OrderRequest, RiskSettings
from app.domain.strategy.base_strategy import Bar, BaseStrategy, SignalType

ZERO = Decimal("0")
_SLIPPAGE_BPS = Decimal("2")  # matches OrderService._SIMULATED_SLIPPAGE_BPS for consistency


@dataclass
class _OpenPosition:
    direction: str  # "long" | "short"
    entry_price: Decimal
    quantity: Decimal
    entry_time: dt.datetime
    stop_loss: Decimal | None
    take_profit: Decimal | None


class BacktestEngine:
    """Stateful per-run (holds no state between `.run()` calls beyond what's
    passed in) — safe to construct fresh per backtest, cheap to do so."""

    def __init__(self, strategy: BaseStrategy, risk_engine: RiskEngine, risk_settings: RiskSettings, config: BacktestConfig):
        self.strategy = strategy
        self.risk_engine = risk_engine
        self.risk_settings = risk_settings
        self.config = config

    def run(self, bars: list[Bar], data_source: str) -> BacktestRunResult:
        trades: list[SimulatedTrade] = []
        equity_curve: list[EquityPoint] = []
        risk_rejections: list[RiskRejection] = []

        balance = self.config.initial_balance
        peak_equity = self.config.initial_balance
        open_position: _OpenPosition | None = None
        daily_loss = ZERO
        current_day: dt.date | None = None

        for i, bar in enumerate(bars):
            bar_time = _parse_timestamp(bar.timestamp)
            bar_date = bar_time.date()
            if current_day is None or bar_date != current_day:
                daily_loss = ZERO
                current_day = bar_date

            # 1. Check exits FIRST, using this bar's intrabar range — a
            # position can be stopped out or hit its target before the
            # strategy even re-evaluates on this bar.
            if open_position is not None:
                exit_hit = self._check_exit(open_position, bar)
                if exit_hit is not None:
                    exit_price_raw, reason = exit_hit
                    trade = self._close_position(open_position, exit_price_raw, bar_time, reason)
                    trades.append(trade)
                    balance += trade.net_profit
                    if trade.net_profit < 0:
                        daily_loss += abs(trade.net_profit)
                    open_position = None

            # 2. Evaluate the strategy on the window ending at this bar.
            window = bars[: i + 1]
            signal = self.strategy.generate_signal(self.config.symbol, window)

            if signal is not None and signal.signal_type != SignalType.HOLD:
                if open_position is not None:
                    if _is_opposite(open_position.direction, signal.signal_type):
                        trade = self._close_position(open_position, bar.close, bar_time, TradeCloseReason.SIGNAL_REVERSAL)
                        trades.append(trade)
                        balance += trade.net_profit
                        if trade.net_profit < 0:
                            daily_loss += abs(trade.net_profit)
                        open_position = None
                    # else: signal agrees with the open position's direction.
                    # Sprint 2 scope: no pyramiding/adding-to-position during
                    # backtests (unlike live OrderService) — ignored.
                else:
                    order_request = self._signal_to_order_request(signal, bar)
                    account_state = self._account_state_for_new_entry(balance, peak_equity, daily_loss)
                    decision = self.risk_engine.evaluate_order(order_request, account_state, self.risk_settings)
                    if decision.approved and decision.approved_position_size:
                        open_position = self._open_position(signal, bar, bar_time, decision.approved_position_size)
                    else:
                        risk_rejections.append(
                            RiskRejection(
                                timestamp=bar_time, signal_type=signal.signal_type,
                                reason=decision.reason or "Risk Engine could not approve this order.",
                            )
                        )

            # 3. Mark-to-market equity for this bar and record the curve point.
            equity = balance + (self._unrealized_pnl(open_position, bar.close) if open_position else ZERO)
            if equity > peak_equity:
                peak_equity = equity
            drawdown = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else ZERO
            equity_curve.append(EquityPoint(timestamp=bar_time, equity=equity, drawdown_pct=drawdown))

        # Force-close anything still open at the end of the test window so
        # metrics reflect a complete picture rather than phantom unrealized PnL.
        if open_position is not None and bars:
            last_bar = bars[-1]
            last_time = _parse_timestamp(last_bar.timestamp)
            trade = self._close_position(open_position, last_bar.close, last_time, TradeCloseReason.END_OF_BACKTEST)
            trades.append(trade)
            balance += trade.net_profit
            if balance > peak_equity:
                peak_equity = balance
            drawdown = ((peak_equity - balance) / peak_equity * 100) if peak_equity > 0 else ZERO
            equity_curve.append(EquityPoint(timestamp=last_time, equity=balance, drawdown_pct=drawdown))

        metrics = compute_metrics(trades, equity_curve, final_balance=balance, final_equity=balance, timeframe=self.config.timeframe)

        return BacktestRunResult(
            config=self.config, trades=trades, equity_curve=equity_curve, risk_rejections=risk_rejections,
            metrics=metrics, bars_processed=len(bars), data_source=data_source,
        )

    # ------------------------------------------------------------------

    def _account_state_for_new_entry(self, balance: Decimal, peak_equity: Decimal, daily_loss: Decimal) -> AccountState:
        # Only ever called when no position is open (see call site), so
        # exposure/position counts are always zero here by construction.
        drawdown_pct = ((peak_equity - balance) / peak_equity * 100) if peak_equity > 0 else ZERO
        return AccountState(
            equity=balance,
            balance=balance,
            open_positions_count=0,
            positions_for_symbol_count=0,
            current_daily_loss=daily_loss,
            current_drawdown_pct=max(ZERO, drawdown_pct),
            current_exposure_by_symbol={},
            current_portfolio_exposure=ZERO,
            kill_switch_active=False,
            # Sprint 2 scope: the kill switch is a live-trading emergency-stop
            # concept. A backtest asks "would this strategy have been allowed
            # to trade under my normal risk limits", not "simulate an
            # emergency halt", so it's always false here.
        )

    def _signal_to_order_request(self, signal, bar: Bar) -> OrderRequest:
        return OrderRequest(
            symbol=self.config.symbol,
            side="buy" if signal.signal_type == SignalType.BUY else "sell",
            order_type="market",
            requested_price=bar.close,
            stop_loss=signal.suggested_stop_loss,
            take_profit=signal.suggested_take_profit,
            quantity=None,  # force risk-based position sizing, same as live trading
            strategy_enabled=True,
        )

    def _open_position(self, signal, bar: Bar, bar_time: dt.datetime, quantity: Decimal) -> _OpenPosition:
        direction = "long" if signal.signal_type == SignalType.BUY else "short"
        entry_price = self._apply_slippage(bar.close, direction, opening=True)
        return _OpenPosition(
            direction=direction, entry_price=entry_price, quantity=quantity, entry_time=bar_time,
            stop_loss=signal.suggested_stop_loss, take_profit=signal.suggested_take_profit,
        )

    def _check_exit(self, position: _OpenPosition, bar: Bar) -> tuple[Decimal, TradeCloseReason] | None:
        if position.direction == "long":
            # Conservative assumption for bar-based (non-tick) data: if both
            # the stop and target fall within this bar's range, assume the
            # stop was hit first (standard, worst-case backtesting practice).
            if position.stop_loss is not None and bar.low <= position.stop_loss:
                return position.stop_loss, TradeCloseReason.STOP_LOSS
            if position.take_profit is not None and bar.high >= position.take_profit:
                return position.take_profit, TradeCloseReason.TAKE_PROFIT
        else:
            if position.stop_loss is not None and bar.high >= position.stop_loss:
                return position.stop_loss, TradeCloseReason.STOP_LOSS
            if position.take_profit is not None and bar.low <= position.take_profit:
                return position.take_profit, TradeCloseReason.TAKE_PROFIT
        return None

    def _close_position(
        self, position: _OpenPosition, exit_price_raw: Decimal, exit_time: dt.datetime, reason: TradeCloseReason
    ) -> SimulatedTrade:
        exit_price = self._apply_slippage(exit_price_raw, position.direction, opening=False)
        if position.direction == "long":
            gross = (exit_price - position.entry_price) * position.quantity
        else:
            gross = (position.entry_price - exit_price) * position.quantity

        entry_notional = position.entry_price * position.quantity
        exit_notional = exit_price * position.quantity
        commission = (entry_notional + exit_notional) * (self.config.commission_pct / 100)

        # Quantize money fields to a fixed 4-decimal-place scale -- matching
        # Trade.net_profit/commission's Numeric(20,4) precision elsewhere in
        # the codebase -- to avoid Decimal producing scientific-notation
        # artifacts like "0E-16" when a value happens to compute to exactly
        # zero (e.g. zero commission_pct, or a rare exact-breakeven trade).
        # Same root cause and same fix pattern as app/core/decimal_utils.py's
        # clean_decimal(), used for the identical reason in the live risk/
        # portfolio endpoints (see 05_API_Specification integration notes).
        money_places = Decimal("0.0001")
        gross = gross.quantize(money_places)
        commission = commission.quantize(money_places)
        net = (gross - commission).quantize(money_places)
        outcome = "win" if net > 0 else "loss" if net < 0 else "breakeven"

        return SimulatedTrade(
            symbol=self.config.symbol, direction=position.direction,
            entry_time=position.entry_time, exit_time=exit_time,
            entry_price=position.entry_price, exit_price=exit_price, quantity=position.quantity,
            commission=commission, gross_profit=gross, net_profit=net, close_reason=reason, outcome=outcome,
        )

    def _unrealized_pnl(self, position: _OpenPosition, current_price: Decimal) -> Decimal:
        if position.direction == "long":
            return (current_price - position.entry_price) * position.quantity
        return (position.entry_price - current_price) * position.quantity

    def _apply_slippage(self, price: Decimal, direction: str, opening: bool) -> Decimal:
        is_buying = (direction == "long" and opening) or (direction == "short" and not opening)
        factor = (1 + _SLIPPAGE_BPS / 10000) if is_buying else (1 - _SLIPPAGE_BPS / 10000)
        return (price * factor).quantize(Decimal("0.00000001"))


def _is_opposite(open_direction: str, signal_type: SignalType) -> bool:
    if open_direction == "long":
        return signal_type == SignalType.SELL
    return signal_type == SignalType.BUY


def _parse_timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed
