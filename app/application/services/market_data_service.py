from __future__ import annotations

import datetime as dt

from app.infrastructure.market_data.provider import (
    get_historical_ohlcv,
    get_latest_price,
    list_supported_symbols,
)


class MarketDataService:
    def symbols(self) -> list[dict]:
        return list_supported_symbols()

    def history(self, symbol: str, timeframe: str, start_date: dt.datetime, end_date: dt.datetime):
        return get_historical_ohlcv(symbol, timeframe, start_date, end_date)

    def latest_price(self, symbol: str):
        return get_latest_price(symbol)
