"""
Market data provider abstraction.

Sprint 1 shipped yfinance-with-mock-fallback as a set of module-level
functions. Sprint 2 formalizes this into a proper interface so new
sources (Alpha Vantage, IEX, a broker's own feed, etc.) can be added
later by implementing `MarketDataProvider` — without the Backtesting
Engine, OrderService, or any router needing to change.

Backward compatibility: the original module-level functions
(`get_historical_ohlcv`, `get_latest_price`, `list_supported_symbols` in
`provider.py`) are preserved unchanged in signature and behavior — they
now delegate to a module-level `FallbackMarketDataProvider` instance
built from this abstraction. Every Sprint 1 call site (OrderService,
MarketDataService, the risk preview endpoint) keeps working with zero
changes.
"""
from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from decimal import Decimal


class OHLCVBar(dict):
    """Plain dict subclass so it's trivially JSON-serializable in responses."""


class MarketDataProviderError(Exception):
    """Raised by a provider implementation on fetch failure. Callers that
    want fallback behavior (see FallbackMarketDataProvider) catch this;
    callers that want a hard failure (rare) let it propagate."""


class MarketDataProvider(ABC):
    """A source of historical and live market data. Implementations must
    be side-effect-free with respect to application state — they only
    read from an external source (or generate synthetic data) and return
    plain data structures."""

    name: str

    @abstractmethod
    def get_historical_ohlcv(
        self, symbol: str, timeframe: str, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[OHLCVBar]:
        """Raise MarketDataProviderError on failure — never return partial
        or fabricated data silently."""
        raise NotImplementedError

    @abstractmethod
    def get_latest_price(self, symbol: str) -> Decimal:
        raise NotImplementedError
