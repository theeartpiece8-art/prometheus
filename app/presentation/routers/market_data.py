import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.application.schemas.market_data import (
    HistoricalDataResponse,
    LatestPriceResponse,
    OHLCVBarResponse,
    SymbolResponse,
)
from app.application.services.market_data_service import MarketDataService
from app.core.dependencies import get_current_active_user
from app.infrastructure.models.user import User

router = APIRouter(prefix="/market", tags=["Market Data"])
_service = MarketDataService()


@router.get("/symbols", response_model=list[SymbolResponse])
def list_symbols(current_user: User = Depends(get_current_active_user)):
    return _service.symbols()


@router.get("/history", response_model=HistoricalDataResponse)
def get_history(
    symbol: str,
    timeframe: str = Query(default="1D"),
    start_date: dt.datetime | None = Query(default=None),
    end_date: dt.datetime | None = Query(default=None),
    current_user: User = Depends(get_current_active_user),
):
    end = end_date or dt.datetime.now(dt.timezone.utc)
    start = start_date or (end - dt.timedelta(days=30))
    if start >= end:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_date must be before end_date.")

    bars, data_source = _service.history(symbol, timeframe, start, end)
    return HistoricalDataResponse(
        symbol=symbol, timeframe=timeframe, start_date=start, end_date=end, data_source=data_source,
        bars=[OHLCVBarResponse(**b) for b in bars],
    )


@router.get("/live", response_model=LatestPriceResponse)
def get_live_price(symbol: str, current_user: User = Depends(get_current_active_user)):
    price, data_source = _service.latest_price(symbol)
    return LatestPriceResponse(
        symbol=symbol, price=float(price), data_source=data_source, as_of=dt.datetime.now(dt.timezone.utc)
    )
