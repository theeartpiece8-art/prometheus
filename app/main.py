"""
PROMETHEUS Quant Lab — FastAPI application entry point.

Run locally with: uvicorn app.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.middleware import RequestLoggingMiddleware
from app.infrastructure.logging.logger import configure_logging, get_logger
from app.presentation.routers import api_router, websocket_router

settings = get_settings()
configure_logging()
logger = get_logger("startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "app.startup",
        extra={"version": __version__, "environment": settings.ENVIRONMENT, "debug": settings.DEBUG},
    )

    # --- Sprint 3: Paper Trading scheduler ---
    # Skipped entirely under tests: pytest drives run_tick() directly and
    # a background thread hitting the real DB engine would fight with the
    # per-test in-memory SQLite override.
    if settings.ENVIRONMENT != "test":
        _mark_interrupted_sessions()
        from app.infrastructure.scheduling.paper_trading_scheduler import scheduler

        await scheduler.start()

    yield

    if settings.ENVIRONMENT != "test":
        from app.infrastructure.scheduling.paper_trading_scheduler import scheduler

        await scheduler.stop()
    logger.info("app.shutdown")


def _mark_interrupted_sessions() -> None:
    """Delegates to paper_trading_service.mark_interrupted_sessions — the
    logic lives there (with a db parameter) so it's directly testable;
    this wrapper just supplies a real SessionLocal and guards startup."""
    from app.application.services.paper_trading_service import mark_interrupted_sessions
    from app.infrastructure.database.session import SessionLocal

    db = SessionLocal()
    try:
        mark_interrupted_sessions(db)
    except Exception:  # noqa: BLE001 — a failure here must not block app startup (e.g. before first migration)
        logger.exception("paper_trading.interrupted_check_failed")
        db.rollback()
    finally:
        db.close()


app = FastAPI(
    title=settings.APP_NAME,
    version=__version__,
    description=(
        "PROMETHEUS Quant Lab backend — Sprint 1: auth, database layer, Risk Management Engine, "
        "simulated order execution. Sprint 2: Backtesting Engine. Sprint 3: Paper Trading Engine "
        "(automated strategy sessions on live data)."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

register_exception_handlers(app)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)
app.include_router(websocket_router)  # /ws/* — not versioned, matches the API spec


@app.get("/", tags=["Root"])
def root():
    return {
        "name": settings.APP_NAME,
        "version": __version__,
        "docs": "/docs",
        "api_prefix": settings.API_V1_PREFIX,
    }
