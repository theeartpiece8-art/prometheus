"""Small shared utility for code that needs to fetch bars from either a
plain MarketDataProvider or the richer FallbackMarketDataProvider (which
can also report which underlying source served the data). Used by both
BacktestService and PaperTradingService — factored out here rather than
duplicated, or imported service-to-service."""
from __future__ import annotations

import datetime as dt

from app.infrastructure.market_data.base_provider import MarketDataProvider, OHLCVBar
from app.infrastructure.market_data.fallback_provider import FallbackMarketDataProvider


def fetch_bars_with_source(
    provider: MarketDataProvider, symbol: str, timeframe: str, start_date: dt.datetime, end_date: dt.datetime
) -> tuple[list[OHLCVBar], str]:
    """Every MarketDataProvider can fetch bars; only the composed
    FallbackMarketDataProvider can also report which underlying source
    actually served them. Handled here as an explicit type check (not
    duck-typed hasattr probing) so the two provider shapes are clear."""
    if isinstance(provider, FallbackMarketDataProvider):
        return provider.get_historical_ohlcv_with_source(symbol, timeframe, start_date, end_date)
    return provider.get_historical_ohlcv(symbol, timeframe, start_date, end_date), provider.name
