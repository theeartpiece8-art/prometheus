"""
Live Trading API. Per the Sprint 1 brief: "Order System (simulation only
for Sprint 1)... no real broker yet." 10_Live_Trading_Engine.md's broker
adapters, connection monitoring, and failure recovery are future work.

Live trading is reported as permanently disabled this sprint — not
because of a runtime check that could theoretically pass, but because the
capability genuinely does not exist yet. This mirrors the Deployment
Guide's own safety rule: "No live trading without risk engine active" —
here, extended to "no live trading without a live trading engine at all."
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import get_current_active_user
from app.infrastructure.models.user import User

router = APIRouter(prefix="/live", tags=["Live Trading (future sprint)"])

_NOT_YET_MESSAGE = (
    "Live trading requires a real broker adapter (10_Live_Trading_Engine.md), which is "
    "out of scope for Sprint 1 by design. Sprint 1 provides simulated order execution "
    "only (see POST /api/v1/orders/place) so strategies can be validated safely before "
    "any real-money integration is built."
)


class LiveStatusResponse(BaseModel):
    enabled: bool
    message: str


@router.post("/enable", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def enable_live_trading(current_user: User = Depends(get_current_active_user)):
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_YET_MESSAGE)


@router.post("/disable", response_model=LiveStatusResponse)
def disable_live_trading(current_user: User = Depends(get_current_active_user)):
    # Always safe to report "disabled" — there is nothing to actually disable yet.
    return LiveStatusResponse(enabled=False, message=_NOT_YET_MESSAGE)


@router.get("/status", response_model=LiveStatusResponse)
def live_trading_status(current_user: User = Depends(get_current_active_user)):
    return LiveStatusResponse(enabled=False, message=_NOT_YET_MESSAGE)
