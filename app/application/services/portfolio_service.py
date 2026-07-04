from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.decimal_utils import clean_decimal
from app.infrastructure.models.equity_history import EquityHistory
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.position import Position
from app.infrastructure.repositories.portfolio_repository import PortfolioRepository
from app.infrastructure.repositories.position_repository import PositionRepository


class PortfolioServiceError(Exception):
    pass


class PortfolioNotFoundError(PortfolioServiceError):
    pass


class PortfolioService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = PortfolioRepository(db)
        self.positions = PositionRepository(db)

    def get_default_for_user(self, user_id: uuid.UUID) -> Portfolio:
        portfolio = self.repo.get_default_for_user(user_id)
        if portfolio is None:
            raise PortfolioNotFoundError(
                "No portfolio exists for this user. This should not happen — "
                "a default portfolio is created automatically at registration."
            )
        return portfolio

    def list_open_positions(self, portfolio: Portfolio) -> list[Position]:
        return self.positions.list_open_for_portfolio(portfolio.id)

    def exposure_breakdown(self, portfolio: Portfolio) -> dict:
        by_symbol = self.positions.exposure_by_symbol(portfolio.id)
        total = sum(by_symbol.values(), Decimal("0"))
        pct = clean_decimal(total / portfolio.equity * 100) if portfolio.equity > 0 else Decimal("0")
        return {"total_exposure": total, "exposure_by_symbol": by_symbol, "portfolio_exposure_pct": pct}

    def equity_history(self, portfolio: Portfolio, limit: int = 200) -> list[EquityHistory]:
        from sqlalchemy import select

        stmt = (
            select(EquityHistory)
            .where(EquityHistory.portfolio_id == portfolio.id)
            .order_by(EquityHistory.timestamp.desc())
            .limit(limit)
        )
        rows = list(self.db.execute(stmt).scalars().all())
        rows.reverse()  # chronological order for charting
        return rows
