"""
Strategy service: CRUD + enable/disable/clone over the Strategy table.

Design note: 04_Database_Design.md's STRATEGIES table has no dedicated
"implementation type" column, but the system needs to know which
BaseStrategy subclass (see app/domain/strategy) a given row corresponds
to. Rather than add an undocumented column, we store it under a reserved
key inside the existing `parameters` JSON blob (`parameters["_strategy_type"]`)
so the schema stays exactly as specified.
"""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.domain.strategy.sample_strategies import STRATEGY_TYPE_KEY, get_strategy_class
from app.infrastructure.models.enums import StrategyStatus
from app.infrastructure.models.strategy import Strategy
from app.infrastructure.repositories.strategy_repository import StrategyRepository

_STRATEGY_TYPE_KEY = STRATEGY_TYPE_KEY  # local alias, kept so the rest of this file needs no other changes


class StrategyServiceError(Exception):
    pass


class StrategyNotFoundError(StrategyServiceError):
    pass


class InvalidStrategyParametersError(StrategyServiceError):
    pass


class StrategyService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = StrategyRepository(db)

    def create(
        self, user_id: uuid.UUID, name: str, strategy_type: str, description: str | None,
        asset_class: str | None, timeframe: str | None, parameters: dict,
    ) -> Strategy:
        strategy_cls = get_strategy_class(strategy_type)
        if strategy_cls is None:
            raise InvalidStrategyParametersError(f"Unknown strategy_type '{strategy_type}'.")

        instance = strategy_cls(parameters=parameters)
        errors = instance.validate_parameters()
        if errors:
            raise InvalidStrategyParametersError("; ".join(errors))

        stored_parameters = {**parameters, _STRATEGY_TYPE_KEY: strategy_type}
        strategy = Strategy(
            user_id=user_id, name=name, description=description, asset_class=asset_class,
            timeframe=timeframe, parameters=stored_parameters, status=StrategyStatus.DRAFT.value,
        )
        self.db.add(strategy)
        self.db.commit()
        self.db.refresh(strategy)
        return strategy

    def get(self, strategy_id: uuid.UUID, user_id: uuid.UUID) -> Strategy:
        strategy = self.repo.get_for_user(strategy_id, user_id)
        if strategy is None:
            raise StrategyNotFoundError("Strategy not found.")
        return strategy

    def list_for_user(self, user_id: uuid.UUID, offset: int = 0, limit: int = 100) -> list[Strategy]:
        return self.repo.list_for_user(user_id, offset=offset, limit=limit)

    def update(self, strategy_id: uuid.UUID, user_id: uuid.UUID, **changes) -> Strategy:
        strategy = self.get(strategy_id, user_id)
        parameters = changes.pop("parameters", None)
        if parameters is not None:
            strategy_type = strategy.parameters.get(_STRATEGY_TYPE_KEY, "moving_average_crossover")
            strategy_cls = get_strategy_class(strategy_type)
            if strategy_cls is not None:
                errors = strategy_cls(parameters=parameters).validate_parameters()
                if errors:
                    raise InvalidStrategyParametersError("; ".join(errors))
            changes["parameters"] = {**parameters, _STRATEGY_TYPE_KEY: strategy_type}

        for field, value in changes.items():
            if value is not None:
                setattr(strategy, field, value)
        self.db.commit()
        self.db.refresh(strategy)
        return strategy

    def delete(self, strategy_id: uuid.UUID, user_id: uuid.UUID) -> None:
        strategy = self.get(strategy_id, user_id)
        self.db.delete(strategy)
        self.db.commit()

    def clone(self, strategy_id: uuid.UUID, user_id: uuid.UUID) -> Strategy:
        original = self.get(strategy_id, user_id)
        clone = Strategy(
            user_id=user_id, name=f"{original.name} (copy)", description=original.description,
            asset_class=original.asset_class, timeframe=original.timeframe,
            parameters=dict(original.parameters), status=StrategyStatus.DRAFT.value,
        )
        self.db.add(clone)
        self.db.commit()
        self.db.refresh(clone)
        return clone

    def set_enabled(self, strategy_id: uuid.UUID, user_id: uuid.UUID, enabled: bool) -> Strategy:
        strategy = self.get(strategy_id, user_id)
        strategy.status = StrategyStatus.ACTIVE.value if enabled else StrategyStatus.DISABLED.value
        self.db.commit()
        self.db.refresh(strategy)
        return strategy
