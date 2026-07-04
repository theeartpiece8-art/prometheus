import datetime as dt

from pydantic import BaseModel


class SymbolResponse(BaseModel):
    symbol: str
    name: str
    asset_class: str


class OHLCVBarResponse(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class HistoricalDataResponse(BaseModel):
    symbol: str
    timeframe: str
    start_date: dt.datetime
    end_date: dt.datetime
    data_source: str
    bars: list[OHLCVBarResponse]


class LatestPriceResponse(BaseModel):
    symbol: str
    price: float
    data_source: str
    as_of: dt.datetime
