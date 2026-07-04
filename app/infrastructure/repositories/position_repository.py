import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infrastructure.models.enums import PositionStatus
from app.infrastructure.models.position import Position
from app.infrastructure.repositories.base_repository import BaseRepository


class PositionRepository(BaseRepository[Position]):
    def __init__(self, db: Session) -> None:
        super().__init__(db, Position)

    def list_open_for_portfolio(self, portfolio_id: uuid.UUID) -> list[Position]:
        stmt = select(Position).where(
            Position.portfolio_id == portfolio_id, Position.status == PositionStatus.OPEN.value
        )
        return list(self.db.execute(stmt).scalars().all())

    def count_open_for_portfolio(self, portfolio_id: uuid.UUID) -> int:
        return len(self.list_open_for_portfolio(portfolio_id))

    def count_open_for_symbol(self, portfolio_id: uuid.UUID, symbol: str) -> int:
        stmt = select(func.count(Position.id)).where(
            Position.portfolio_id == portfolio_id,
            Position.symbol == symbol,
            Position.status == PositionStatus.OPEN.value,
        )
        return self.db.execute(stmt).scalar_one()

    def get_open_for_symbol(self, portfolio_id: uuid.UUID, symbol: str) -> Position | None:
        stmt = select(Position).where(
            Position.portfolio_id == portfolio_id,
            Position.symbol == symbol,
            Position.status == PositionStatus.OPEN.value,
        )
        return self.db.execute(stmt).scalars().first()

    def exposure_by_symbol(self, portfolio_id: uuid.UUID) -> dict[str, Decimal]:
        """Notional exposure (quantity * average_price) per symbol, open positions only."""
        exposures: dict[str, Decimal] = {}
        for pos in self.list_open_for_portfolio(portfolio_id):
            notional = pos.quantity * pos.average_price
            exposures[pos.symbol] = exposures.get(pos.symbol, Decimal("0")) + notional
        return exposures

    def total_exposure(self, portfolio_id: uuid.UUID) -> Decimal:
        return sum(self.exposure_by_symbol(portfolio_id).values(), Decimal("0"))
