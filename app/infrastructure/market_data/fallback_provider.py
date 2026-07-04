"""
Composes a primary provider with a fallback provider, trying the primary
first and falling back transparently on any `MarketDataProviderError` (or
if live fetch is disabled via config). This is the provider actually used
by the application by default (yfinance primary, mock fallback) — see
provider.py.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.infrastructure.logging.logger import get_logger
from app.infrastructure.market_data.base_provider import MarketDataProvider, MarketDataProviderError, OHLCVBar

logger = get_logger("market_data")


class FallbackMarketDataProvider(MarketDataProvider):
    name = "fallback"

    def __init__(self, primary: MarketDataProvider, fallback: MarketDataProvider, primary_enabled: bool = True) -> None:
        self.primary = primary
        self.fallback = fallback
        self.primary_enabled = primary_enabled

    def get_historical_ohlcv_with_source(
        self, symbol: str, timeframe: str, start_date: dt.datetime, end_date: dt.datetime
    ) -> tuple[list[OHLCVBar], str]:
        """Like get_historical_ohlcv, but also returns which provider
        actually served the data — used by callers (BacktestService,
        market_data.py router) that must disclose data provenance."""
        if self.primary_enabled:
            try:
                bars = self.primary.get_historical_ohlcv(symbol, timeframe, start_date, end_date)
                return bars, self.primary.name
            except MarketDataProviderError as exc:
                logger.warning(
                    "market_data.primary_fetch_failed",
                    extra={"symbol": symbol, "provider": self.primary.name, "error": str(exc)},
                )
        return self.fallback.get_historical_ohlcv(symbol, timeframe, start_date, end_date), self.fallback.name

    def get_latest_price_with_source(self, symbol: str) -> tuple[Decimal, str]:
        if self.primary_enabled:
            try:
                return self.primary.get_latest_price(symbol), self.primary.name
            except MarketDataProviderError as exc:
                logger.warning(
                    "market_data.primary_price_fetch_failed",
                    extra={"symbol": symbol, "provider": self.primary.name, "error": str(exc)},
                )
        return self.fallback.get_latest_price(symbol), self.fallback.name

    # --- MarketDataProvider interface (source-agnostic convenience) ---

    def get_historical_ohlcv(
        self, symbol: str, timeframe: str, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[OHLCVBar]:
        bars, _source = self.get_historical_ohlcv_with_source(symbol, timeframe, start_date, end_date)
        return bars

    def get_latest_price(self, symbol: str) -> Decimal:
        price, _source = self.get_latest_price_with_source(symbol)
        return price
