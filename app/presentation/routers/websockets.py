"""
WebSocket channels, per 05_API_Specification.md: /ws/market, /ws/orders,
/ws/positions, /ws/portfolio, /ws/notifications, /ws/system.

Sprint 1 scope: these are REAL WebSocket endpoints — they accept
connections, authenticate via a `token` query param (WebSocket clients
can't send Authorization headers), and push periodic snapshots read
straight from the database. What's NOT implemented yet is push-on-change
from a live market data feed / order event bus (that requires the
real-time infrastructure noted as future work in 03_System_Architecture.md).
Rather than fake a live feed, each channel currently pushes a snapshot on
connect and then a heartbeat every `_HEARTBEAT_SECONDS` — genuinely
working infrastructure that a future event-driven push layer can plug
into without changing the wire protocol.
"""
import asyncio
import datetime as dt
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.infrastructure.database.session import SessionLocal
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.security.jwt import TokenError, TokenType, decode_token

logger = get_logger("websockets")
router = APIRouter(tags=["WebSocket Channels"])

_HEARTBEAT_SECONDS = 15


async def _authenticate(websocket: WebSocket, token: str | None) -> str | None:
    if not token:
        await websocket.close(code=4401, reason="Missing token query parameter.")
        return None
    try:
        decoded = decode_token(token, expected_type=TokenType.ACCESS)
    except TokenError as exc:
        await websocket.close(code=4401, reason=str(exc))
        return None
    return str(decoded.user_id)


async def _channel_loop(websocket: WebSocket, channel: str, snapshot_fn) -> None:
    await websocket.accept()
    try:
        while True:
            payload = {"channel": channel, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat()}
            if snapshot_fn is not None:
                payload["data"] = snapshot_fn()
            else:
                payload["data"] = None
                payload["note"] = (
                    "Live push updates are future work; this is a periodic heartbeat "
                    "confirming the channel is connected."
                )
            await websocket.send_text(json.dumps(payload, default=str))
            await asyncio.sleep(_HEARTBEAT_SECONDS)
    except WebSocketDisconnect:
        logger.info("websocket.disconnected", extra={"channel": channel})


def _system_health_snapshot() -> dict:
    db = SessionLocal()
    try:
        from sqlalchemy import text

        db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception:  # noqa: BLE001
        db_status = "unreachable"
    finally:
        db.close()
    return {"database": db_status}


@router.websocket("/ws/market")
async def ws_market(websocket: WebSocket, token: str | None = Query(default=None)):
    if await _authenticate(websocket, token) is None:
        return
    await _channel_loop(websocket, "market", None)


@router.websocket("/ws/orders")
async def ws_orders(websocket: WebSocket, token: str | None = Query(default=None)):
    if await _authenticate(websocket, token) is None:
        return
    await _channel_loop(websocket, "orders", None)


@router.websocket("/ws/positions")
async def ws_positions(websocket: WebSocket, token: str | None = Query(default=None)):
    if await _authenticate(websocket, token) is None:
        return
    await _channel_loop(websocket, "positions", None)


@router.websocket("/ws/portfolio")
async def ws_portfolio(websocket: WebSocket, token: str | None = Query(default=None)):
    if await _authenticate(websocket, token) is None:
        return
    await _channel_loop(websocket, "portfolio", None)


@router.websocket("/ws/notifications")
async def ws_notifications(websocket: WebSocket, token: str | None = Query(default=None)):
    if await _authenticate(websocket, token) is None:
        return
    await _channel_loop(websocket, "notifications", None)


@router.websocket("/ws/system")
async def ws_system(websocket: WebSocket, token: str | None = Query(default=None)):
    if await _authenticate(websocket, token) is None:
        return
    await _channel_loop(websocket, "system", _system_health_snapshot)
