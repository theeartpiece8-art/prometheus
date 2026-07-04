import datetime as dt
import uuid

from pydantic import BaseModel, Field

from app.application.schemas.common import ORMModel


class StrategyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    strategy_type: str = Field(
        default="moving_average_crossover",
        description="Registered strategy implementation to use (see STRATEGY_REGISTRY).",
    )
    description: str | None = None
    asset_class: str | None = None
    timeframe: str | None = None
    parameters: dict = Field(default_factory=dict)


class StrategyUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    asset_class: str | None = None
    timeframe: str | None = None
    parameters: dict | None = None


class StrategyResponse(ORMModel):
    id: uuid.UUID
    name: str
    version: str
    description: str | None
    asset_class: str | None
    timeframe: str | None
    parameters: dict
    status: str
    created_at: dt.datetime
    updated_at: dt.datetime
