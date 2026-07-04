"""Real market data via yfinance. Raises MarketDataProviderError on any
failure (network, bad symbol, rate limit, empty result) rather than
falling back internally — fallback composition is a separate concern,
handled by FallbackMarketDataProvider in provider.py."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.infrastructure.market_data.base_provider import MarketDataProvider, MarketDataProviderError, OHLCVBar

_TIMEFRAME_TO_YF_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1H": "60m", "4H": "60m", "1D": "1d", "daily": "1d", "1W": "1wk", "weekly": "1wk",
}


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    def get_historical_ohlcv(
        self, symbol: str, timeframe: str, start_date: dt.datetime, end_date: dt.datetime
    ) -> list[OHLCVBar]:
        try:
            import yfinance as yf

            interval = _TIMEFRAME_TO_YF_INTERVAL.get(timeframe, "1d")
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date, interval=interval)
        except Exception as exc:  # noqa: BLE001
            raise MarketDataProviderError(f"yfinance fetch failed for {symbol}: {exc}") from exc

        bars: list[OHLCVBar] = []
        for idx, row in df.iterrows():
            bars.append(
                OHLCVBar(
                    timestamp=idx.isoformat(),
                    open=round(float(row["Open"]), 6),
                    high=round(float(row["High"]), 6),
                    low=round(float(row["Low"]), 6),
                    close=round(float(row["Close"]), 6),
                    volume=int(row["Volume"]) if "Volume" in row else 0,
                )
            )
        if not bars:
            raise MarketDataProviderError(f"yfinance returned no rows for {symbol}")
        return bars

    def get_latest_price(self, symbol: str) -> Decimal:
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            fast_info = ticker.fast_info
            price = fast_info.get("lastPrice") or fast_info.get("last_price")
        except Exception as exc:  # noqa: BLE001
            raise MarketDataProviderError(f"yfinance latest-price fetch failed for {symbol}: {exc}") from exc

        if not price:
            raise MarketDataProviderError(f"yfinance returned no price for {symbol}")
        return Decimal(str(round(price, 6)))
