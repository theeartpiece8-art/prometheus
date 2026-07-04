"""
Backtesting API — Sprint 2: the Backtesting Engine is now fully
implemented (see app/domain/backtesting and
app/application/services/backtest_service.py). This router contains NO
business logic: every handler does request parsing -> service call ->
response-schema construction, nothing else, per Sprint 2's explicit "no
logic in controllers" requirement. Response shaping itself lives in
app/application/schemas/backtest.py (build_result_response /
build_summary_response) rather than inline here.

Route registration order matters: GET /history (a static path segment)
is registered BEFORE GET /{job_id} (a dynamic single-segment path). If
/{job_id} were registered first, a request to /history would match that
pattern first (Starlette matches the URL shape before FastAPI validates
the UUID type), producing a 422 instead of reaching the history handler.
GET /results/{id} and GET /report/{id} are two-segment paths and don't
collide with /{job_id} regardless of order, but are kept below /history
for readability.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.application.schemas.backtest import (
    BacktestJobSummaryResponse,
    BacktestResultResponse,
    BacktestRunRequest,
    build_result_response,
    build_summary_response,
)
from app.application.services.backtest_service import (
    BacktestJobNotFoundError,
    BacktestService,
    StrategyNotFoundError,
)
from app.core.dependencies import get_current_active_user, get_db
from app.infrastructure.models.user import User

router = APIRouter(prefix="/backtest", tags=["Backtesting"])


@router.post("/run", response_model=BacktestResultResponse, status_code=status.HTTP_201_CREATED)
def run_backtest(
    payload: BacktestRunRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """
    Executes the backtest synchronously and returns the full result
    (metrics, trade list, equity curve) directly — per Sprint 2's
    "Output: backtest result JSON endpoint" requirement. The underlying
    job is also persisted (see GET /history and GET /results/{id}) so a
    completed run remains queryable afterwards.

    A run that fails (e.g. no historical data for the requested range)
    still returns 201: the request WAS processed and the job WAS
    recorded — `status: "failed"` and `error_message` in the body carry
    the outcome, consistent with how POST /orders/place always returns
    201 even for a risk-rejected order (see orders.py). Only a missing
    strategy (a genuinely invalid request) is a 404.
    """
    service = BacktestService(db)
    try:
        job = service.run_backtest(current_user.id, payload)
    except StrategyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return build_result_response(job)


@router.get("/history", response_model=list[BacktestJobSummaryResponse])
def backtest_history(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    jobs = BacktestService(db).list_for_user(current_user.id)
    return [build_summary_response(j) for j in jobs]


@router.get("/results/{job_id}", response_model=BacktestResultResponse)
def get_backtest_results(
    job_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """Sprint 2's primary result-retrieval path."""
    try:
        job = BacktestService(db).get_for_user(job_id, current_user.id)
    except BacktestJobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return build_result_response(job)


@router.get("/{job_id}", response_model=BacktestResultResponse)
def get_backtest(
    job_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)
):
    """
    Kept for compatibility with 05_API_Specification.md's originally
    documented `GET /api/v1/backtest/{job_id}` path (and Sprint 1's stub
    of the same). Identical behavior to GET /results/{job_id} — use
    whichever path your client already expects.
    """
    try:
        job = BacktestService(db).get_for_user(job_id, current_user.id)
    except BacktestJobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return build_result_response(job)


@router.get("/report/{report_id}")
def download_backtest_report(report_id: uuid.UUID):
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="PDF/CSV/Excel report file generation is planned for a future sprint. "
        "The full result data is already available via GET /backtest/results/{job_id}.",
    )
