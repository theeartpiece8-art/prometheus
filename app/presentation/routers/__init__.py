from fastapi import APIRouter

from app.presentation.routers import (
    ai_assistant,
    analytics,
    auth,
    backtest,
    brokers,
    dashboard,
    health,
    live_trading,
    market_data,
    notifications,
    orders,
    paper_trading,
    portfolio,
    positions,
    reports,
    risk,
    settings,
    strategies,
    watchlists,
    websockets,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(market_data.router)
api_router.include_router(watchlists.router)
api_router.include_router(strategies.router)
api_router.include_router(backtest.router)
api_router.include_router(paper_trading.router)
api_router.include_router(live_trading.router)
api_router.include_router(orders.router)
api_router.include_router(positions.router)
api_router.include_router(portfolio.router)
api_router.include_router(risk.router)
api_router.include_router(analytics.router)
api_router.include_router(reports.router)
api_router.include_router(notifications.router)
api_router.include_router(settings.router)
api_router.include_router(brokers.router)
api_router.include_router(ai_assistant.router)

# WebSocket routes are NOT versioned under /api/v1 (matches the spec's own
# "/ws/..." paths, listed separately from the REST endpoints).
websocket_router = websockets.router

__all__ = ["api_router", "websocket_router"]
