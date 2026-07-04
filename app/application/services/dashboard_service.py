from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.application.services.notification_service import NotificationService
from app.application.services.portfolio_service import PortfolioService
from app.application.services.watchlist_service import WatchlistService
from app.infrastructure.repositories.strategy_repository import StrategyRepository


class DashboardService:
    """
    Aggregates GET /api/v1/dashboard, per 05_API_Specification.md: "account
    summary, portfolio summary, open positions, watchlist, latest
    notifications, system health". Purely a read-side composition of other
    services/repositories — owns no business logic of its own.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.portfolio_service = PortfolioService(db)
        self.notification_service = NotificationService(db)
        self.watchlist_service = WatchlistService(db)
        self.strategies = StrategyRepository(db)

    def build(self, user_id: uuid.UUID) -> dict:
        portfolio = self.portfolio_service.get_default_for_user(user_id)
        open_positions = self.portfolio_service.list_open_positions(portfolio)
        watchlists = self.watchlist_service.list_for_user(user_id)
        notifications = self.notification_service.list_for_user(user_id, limit=10)
        strategies = self.strategies.list_for_user(user_id)

        return {
            "portfolio": portfolio,
            "open_positions": open_positions,
            "watchlists": watchlists,
            "latest_notifications": notifications,
            "strategy_count": len(strategies),
            "active_strategy_count": sum(1 for s in strategies if s.status == "active"),
            "system_health": "healthy",
        }
