"""
Reports API. Listing is real (queries the PerformanceReport table).
PDF/CSV/Excel generation (06_UI_UX_Specification.md's "Reports" screen)
is future work — no report files exist to download yet in Sprint 1 since
nothing generates them (Backtesting Engine execution is also future work).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.performance_report import PerformanceReport
from app.infrastructure.models.strategy import Strategy
from app.infrastructure.models.user import User

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.get("")
def list_reports(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    stmt = select(PerformanceReport).join(Strategy).where(Strategy.user_id == current_user.id)
    reports = db.execute(stmt).scalars().all()
    return [
        {"id": str(r.id), "report_name": r.report_name, "generated_at": r.generated_at.isoformat()} for r in reports
    ]


@router.get("/{report_id}")
def download_report(report_id: uuid.UUID):
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Report file generation (PDF/CSV/Excel) is planned for a future sprint.",
    )
