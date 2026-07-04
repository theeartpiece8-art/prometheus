"""
Sample concrete strategies, demonstrating the BaseStrategy framework end to
end. These are intentionally simple, well-understood, textbook strategies —
Sprint 1 asks for "a basic framework only", not a production alpha library.
"""
from __future__ import annotations

from decimal import Decimal

from app.domain.strategy.base_strategy import BaseStrategy, Bar, SignalType, StrategySignal


class MovingAverageCrossoverStrategy(BaseStrategy):
    """
    Classic fast/slow SMA crossover:
      - fast SMA crosses above slow SMA  -> BUY
      - fast SMA crosses below slow SMA  -> SELL
      - otherwise                        -> no signal

    Parameters:
      fast_period (int, default 10)
      slow_period (int, default 30)
      stop_loss_pct (float, default 2.0)   -- suggested stop distance, %
      take_profit_pct (float, default 4.0) -- suggested target distance, %
    """

    name = "moving_average_crossover"

    def validate_parameters(self) -> list[str]:
        errors = []
        fast = self.parameters.get("fast_period", 10)
        slow = self.parameters.get("slow_period", 30)
        if not isinstance(fast, int) or fast <= 0:
            errors.append("fast_period must be a positive integer")
        if not isinstance(slow, int) or slow <= 0:
            errors.append("slow_period must be a positive integer")
        if isinstance(fast, int) and isinstance(slow, int) and fast >= slow:
            errors.append("fast_period must be smaller than slow_period")
        return errors

    def generate_signal(self, symbol: str, bars: list[Bar]) -> StrategySignal | None:
        fast_period = int(self.parameters.get("fast_period", 10))
        slow_period = int(self.parameters.get("slow_period", 30))
        stop_loss_pct = Decimal(str(self.parameters.get("stop_loss_pct", 2.0)))
        take_profit_pct = Decimal(str(self.parameters.get("take_profit_pct", 4.0)))

        # Need at least slow_period+1 bars to detect a *crossover* (a change
        # in relative position between this bar and the previous one).
        if len(bars) < slow_period + 1:
            return None

        closes = [b.close for b in bars]

        def sma(values: list[Decimal], period: int) -> Decimal:
            window = values[-period:]
            return sum(window) / Decimal(len(window))

        fast_now = sma(closes, fast_period)
        slow_now = sma(closes, slow_period)
        fast_prev = sma(closes[:-1], fast_period)
        slow_prev = sma(closes[:-1], slow_period)

        crossed_up = fast_prev <= slow_prev and fast_now > slow_now
        crossed_down = fast_prev >= slow_prev and fast_now < slow_now

        last_close = closes[-1]

        if crossed_up:
            return StrategySignal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                confidence=Decimal("0.6"),
                suggested_stop_loss=last_close * (1 - stop_loss_pct / 100),
                suggested_take_profit=last_close * (1 + take_profit_pct / 100),
                reason=f"Fast SMA({fast_period})={fast_now:.4f} crossed above Slow SMA({slow_period})={slow_now:.4f}",
            )
        if crossed_down:
            return StrategySignal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                confidence=Decimal("0.6"),
                suggested_stop_loss=last_close * (1 + stop_loss_pct / 100),
                suggested_take_profit=last_close * (1 - take_profit_pct / 100),
                reason=f"Fast SMA({fast_period})={fast_now:.4f} crossed below Slow SMA({slow_period})={slow_now:.4f}",
            )
        return None


STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    MovingAverageCrossoverStrategy.name: MovingAverageCrossoverStrategy,
}

STRATEGY_TYPE_KEY = "_strategy_type"
"""Reserved key under which the strategy implementation type is stored
inside Strategy.parameters (see strategy_service.py's docstring for why:
04_Database_Design.md's STRATEGIES table has no dedicated column for it).
Single source of truth — strategy_service.py, backtest_service.py, and
paper_trading_service.py all extract/inject this the same way via
instantiate_strategy() below, rather than each re-implementing it."""


def get_strategy_class(name: str) -> type[BaseStrategy] | None:
    return STRATEGY_REGISTRY.get(name)


def instantiate_strategy(
    parameters: dict, default_type: str = "moving_average_crossover"
) -> tuple[BaseStrategy, str]:
    """Given a Strategy row's raw `parameters` JSON (which may contain the
    reserved STRATEGY_TYPE_KEY), return (instantiated BaseStrategy, the
    resolved strategy_type string). Raises ValueError for an unregistered
    strategy_type — callers translate this to their own error type."""
    strategy_type = parameters.get(STRATEGY_TYPE_KEY, default_type)
    strategy_cls = get_strategy_class(strategy_type)
    if strategy_cls is None:
        raise ValueError(f"Unknown strategy_type '{strategy_type}'")
    clean_params = {k: v for k, v in parameters.items() if k != STRATEGY_TYPE_KEY}
    return strategy_cls(parameters=clean_params), strategy_type
