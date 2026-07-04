"""
Composition root for market data access.

Sprint 1 built this as a set of standalone functions with yfinance +
inline mock-fallback logic. Sprint 2 (per the explicit "future provider
abstraction" requirement) moves the actual provider logic into
base_provider.py / yfinance_provider.py / mock_provider.py /
fallback_provider.py, and this module becomes a thin composition root
that builds the default `yfinance-with-mock-fallback` provider instance
and re-exposes it through the ORIGINAL function signatures.

This means every Sprint 1 call site (OrderService, MarketDataService, the
risk preview endpoint) keeps working completely unchanged — they still
import `get_historical_ohlcv`, `get_latest_price`, `list_supported_symbols`
from this exact module path.

New code (e.g. BacktestService) can instead import the provider classes
directly from this package for more explicit control — see
app/domain/backtesting and app/application/services/backtest_service.py.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.config import get_settings
from app.infrastructure.market_data.base_provider import MarketDataProvider, OHLCVBar
from app.infrastructure.market_data.fallback_provider import FallbackMarketDataProvider
from app.infrastructure.market_data.mock_provider import MockProvider
from app.infrastructure.market_data.yfinance_provider import YFinanceProvider

settings = get_settings()

# A deliberately small, curated instrument list for Sprint 1 (GET /market/symbols).
# Structure mirrors 07_Risk_Management_Engine.md's asset-class taxonomy so the
# same list can back the "Allowed Symbols" risk setting UI later.
SUPPORTED_SYMBOLS = [
    {"symbol": "AAPL", "name": "Apple Inc.", "asset_class": "stocks"},
    {"symbol": "MSFT", "name": "Microsoft Corp.", "asset_class": "stocks"},
    {"symbol": "TSLA", "name": "Tesla Inc.", "asset_class": "stocks"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "asset_class": "etf"},
    {"symbol": "BTC-USD", "name": "Bitcoin / USD", "asset_class": "crypto"},
    {"symbol": "ETH-USD", "name": "Ethereum / USD", "asset_class": "crypto"},
    {"symbol": "EURUSD=X", "name": "Euro / US Dollar", "asset_class": "forex"},
    {"symbol": "GBPUSD=X", "name": "British Pound / US Dollar", "asset_class": "forex"},
    {"symbol": "GC=F", "name": "Gold Futures", "asset_class": "metals"},
    {"symbol": "^GSPC", "name": "S&P 500 Index", "asset_class": "indices"},
]

# The default provider used everywhere in the app unless a caller explicitly
# constructs its own (e.g. a test injecting a bare MockProvider). Built once
# at import time; cheap (no network/DB calls happen at construction).
default_provider = FallbackMarketDataProvider(
    primary=YFinanceProvider(),
    fallback=MockProvider(),
    primary_enabled=settings.MARKET_DATA_ALLOW_LIVE_FETCH,
)


def list_supported_symbols() -> list[dict]:
    return SUPPORTED_SYMBOLS


def get_historical_ohlcv(
    symbol: str, timeframe: str, start_date: dt.datetime, end_date: dt.datetime
) -> tuple[list[OHLCVBar], str]:
    """Returns (bars, data_source) where data_source is 'yfinance' or 'mock'."""
    return default_provider.get_historical_ohlcv_with_source(symbol, timeframe, start_date, end_date)


def get_latest_price(symbol: str) -> tuple[Decimal, str]:
    """Returns (price, data_source). Used by OrderService to price market orders."""
    return default_provider.get_latest_price_with_source(symbol)
