"""
Broker Integration API. GET /brokers is real (the supported-broker list is
static config, per 10_Live_Trading_Engine.md's "Supported Brokers"
section). Connect/disconnect are stubs — see live_trading.py for why real
broker connectivity is out of scope for Sprint 1.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.broker_account import BrokerAccount
from app.infrastructure.models.user import User

router = APIRouter(prefix="/brokers", tags=["Broker Integration"])

SUPPORTED_BROKERS = [
    {"name": "MetaTrader 5", "id": "mt5", "status": "planned", "asset_classes": ["forex", "metals", "indices"]},
    {"name": "Binance", "id": "binance", "status": "planned", "asset_classes": ["crypto"]},
    {"name": "OANDA", "id": "oanda", "status": "future", "asset_classes": ["forex"]},
    {"name": "Interactive Brokers", "id": "ibkr", "status": "future", "asset_classes": ["stocks", "forex", "etf"]},
    {"name": "Alpaca", "id": "alpaca", "status": "future", "asset_classes": ["stocks", "crypto"]},
]


class BrokerAccountResponse(BaseModel):
    id: uuid.UUID
    broker_name: str
    account_type: str
    status: str


@router.get("")
def list_brokers():
    return SUPPORTED_BROKERS


@router.post("/connect", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def connect_broker(current_user: User = Depends(get_current_active_user)):
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Real broker connectivity is out of scope for Sprint 1 (simulation only). "
        "See 10_Live_Trading_Engine.md for the full adapter interface planned for a future sprint.",
    )


@router.delete("/disconnect")
def disconnect_broker(current_user: User = Depends(get_current_active_user)):
    return {"detail": "No broker is connected (Sprint 1 has no real broker integration)."}


@router.get("/status", response_model=list[BrokerAccountResponse])
def broker_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    from sqlalchemy import select

    stmt = select(BrokerAccount).where(BrokerAccount.user_id == current_user.id)
    accounts = db.execute(stmt).scalars().all()
    return accounts
