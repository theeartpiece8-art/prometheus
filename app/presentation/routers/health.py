import datetime as dt

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.core.dependencies import get_db

router = APIRouter(tags=["Health"])
settings = get_settings()


class HealthResponse(BaseModel):
    status: str
    timestamp: dt.datetime
    database: str
    redis: str


class VersionResponse(BaseModel):
    version: str
    environment: str


@router.get("/health", response_model=HealthResponse)
def health_check(response: Response, db: Session = Depends(get_db)):
    db_status = "healthy"
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_status = "unreachable"

    redis_status = "healthy"
    if settings.REDIS_ENABLED:
        try:
            import redis as redis_lib

            client = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=1)
            client.ping()
        except Exception:  # noqa: BLE001
            redis_status = "unreachable (falling back to in-memory)"
    else:
        redis_status = "disabled"

    overall = "healthy" if db_status == "healthy" else "critical"
    if overall != "healthy":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(status=overall, timestamp=dt.datetime.now(dt.timezone.utc), database=db_status, redis=redis_status)


@router.get("/version", response_model=VersionResponse)
def get_version():
    return VersionResponse(version=__version__, environment=settings.ENVIRONMENT)
