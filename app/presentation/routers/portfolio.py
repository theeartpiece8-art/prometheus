from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.application.schemas.portfolio import EquityHistoryPointResponse, PortfolioExposureResponse, PortfolioResponse
from app.application.services.portfolio_service import PortfolioService
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.user import User

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


@router.get("", response_model=PortfolioResponse)
def get_portfolio(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    return PortfolioService(db).get_default_for_user(current_user.id)


@router.get("/history", response_model=list[EquityHistoryPointResponse])
def get_portfolio_history(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    service = PortfolioService(db)
    portfolio = service.get_default_for_user(current_user.id)
    return service.equity_history(portfolio)


@router.get("/exposure", response_model=PortfolioExposureResponse)
def get_portfolio_exposure(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    service = PortfolioService(db)
    portfolio = service.get_default_for_user(current_user.id)
    return service.exposure_breakdown(portfolio)
