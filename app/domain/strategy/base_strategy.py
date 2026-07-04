"""
Strategy interface, per 03_System_Architecture.md ("Strategies are
plugins... No strategy modifies the platform itself") and Sprint 1 scope
("Strategy interface class, Signal generation structure").

A Strategy is a pure function of market data -> an optional trade signal.
It has NO access to the Risk Engine, the database, brokers, or execution —
by construction, a strategy can only ever *propose* a signal. Everything
downstream of that proposal (sizing, approval, execution) is owned by the
Risk Engine and OrderService, matching "No trade bypasses this module"
in 07_Risk_Management_Engine.md.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class SignalType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar of market data."""
    timestamp: str  # ISO-8601
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    signal_type: SignalType
    confidence: Decimal | None = None
    suggested_stop_loss: Decimal | None = None
    suggested_take_profit: Decimal | None = None
    reason: str = ""


class BaseStrategy(ABC):
    """
    All strategies must extend this class. Subclasses implement
    `generate_signal`, receiving a rolling window of historical bars
    (oldest first, most recent last) and returning at most one signal.

    `parameters` mirrors the Strategy.parameters JSON column — the same
    dict is what gets persisted to and loaded from the database, so
    every parameter a strategy uses must be JSON-serializable.
    """

    name: str = "BaseStrategy"

    def __init__(self, parameters: dict | None = None) -> None:
        self.parameters = parameters or {}

    @abstractmethod
    def generate_signal(self, symbol: str, bars: list[Bar]) -> StrategySignal | None:
        """Return a StrategySignal, or None if there is nothing to do."""
        raise NotImplementedError

    def validate_parameters(self) -> list[str]:
        """Return a list of human-readable validation errors (empty = valid).
        Subclasses should override to validate their own required parameters."""
        return []
