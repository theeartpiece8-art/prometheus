from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.application.schemas.notification import NotificationResponse
from app.application.schemas.portfolio import PortfolioResponse
from app.application.schemas.position import PositionResponse
from app.application.schemas.watchlist import WatchlistResponse
from app.application.services.dashboard_service import DashboardService
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.user import User
from pydantic import BaseModel

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class DashboardResponse(BaseModel):
    portfolio: PortfolioResponse
    open_positions: list[PositionResponse]
    watchlists: list[WatchlistResponse]
    latest_notifications: list[NotificationResponse]
    strategy_count: int
    active_strategy_count: int
    system_health: str


@router.get("", response_model=DashboardResponse)
def get_dashboard(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    service = DashboardService(db)
    data = service.build(current_user.id)
    return DashboardResponse(
        portfolio=PortfolioResponse.model_validate(data["portfolio"]),
        open_positions=[PositionResponse.model_validate(p) for p in data["open_positions"]],
        watchlists=[WatchlistResponse.model_validate(w) for w in data["watchlists"]],
        latest_notifications=[NotificationResponse.model_validate(n) for n in data["latest_notifications"]],
        strategy_count=data["strategy_count"],
        active_strategy_count=data["active_strategy_count"],
        system_health=data["system_health"],
    )
