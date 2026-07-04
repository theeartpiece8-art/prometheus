import uuid

from pydantic import BaseModel, Field

from app.application.schemas.common import ORMModel


class WatchlistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    symbols: list[str] = Field(default_factory=list)


class WatchlistUpdateRequest(BaseModel):
    name: str | None = None
    symbols: list[str] | None = None


class WatchlistResponse(ORMModel):
    id: uuid.UUID
    name: str
    symbols: list[str]
