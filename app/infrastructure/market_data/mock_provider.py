"""Deterministic synthetic OHLCV generator — the fallback data source when
live fetch is unavailable/disabled, and a fully offline-capable provider
in its own right (used directly by tests). Never raises: synthetic data
generation has no external failure mode."""
from __future__ import annotations

import datetime as dt
import hashlib
import math
from decimal import Decimal

from app.infrastructure.market_data.base_provider import MarketDataProvider, OHLCVBar

_TIMEFRAME_TO_TIMEDELTA = {
    "1m": dt.timedelta(minutes=1), "5m": dt.timedelta(minutes=5),
    "15m": dt.timedelta(minutes=15), "30m": dt.timedelta(minutes=30),
    "1H": dt.timedelta(hours=1), "4H": dt.timedelta(hours=4),
    "1D": dt.timedelta(days=1), "daily": dt.timedelta(days=1),
    "1W": dt.timedelta(weeks=1), "weekly": dt.timedelta(weeks=1),
}


class MockProvider(MarketDataProvider):
    name = "mock"

    def get_historical_ohlcv(
        self, symbol: str, timeframe: str, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[OHLCVBar]:
        seed = int(hashlib.sha256(symbol.encode()).hexdigest(), 16) % (2**32)
        step = _TIMEFRAME_TO_TIMEDELTA.get(timeframe, dt.timedelta(days=1))
        bars: list[OHLCVBar] = []

        price = 50 + (seed % 200)  # deterministic base price 50-250 per symbol
        t = start_date
        i = 0
        state = seed or 1
        while t <= end_date and len(bars) < 500:
            state = (1103515245 * state + 12345) % (2**31)
            drift = (state % 2001 - 1000) / 100000  # +/- 1%
            wobble = math.sin(i / 7.0) * 0.003
            price = max(0.01, price * (1 + drift + wobble))
            open_ = price
            high = price * (1 + abs(drift) + 0.001)
            low = price * (1 - abs(drift) - 0.001)
            close = price * (1 + drift / 2)
            volume = 1000 + (state % 5000)
            bars.append(
                OHLCVBar(
                    timestamp=t.isoformat(),
                    open=round(open_, 4), high=round(high, 4), low=round(low, 4),
                    close=round(close, 4), volume=volume,
                )
            )
            t += step
            i += 1
        return bars

    def get_latest_price(self, symbol: str) -> Decimal:
        now = dt.datetime.now(dt.timezone.utc)
        bars = self.get_historical_ohlcv(symbol, "1D", now - dt.timedelta(days=1), now)
        return Decimal(str(bars[-1]["close"])) if bars else Decimal("100")
