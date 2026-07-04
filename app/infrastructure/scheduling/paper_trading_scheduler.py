"""
Background scheduler for Paper Trading sessions.

Deliberately thin: ALL trading logic lives in PaperTradingService.run_tick()
(synchronous, directly testable). This module only handles the "when":
a single asyncio task wakes up every POLL_INTERVAL_SECONDS, finds sessions
whose tick is due (status == running AND enough time elapsed since
last_tick_at per that session's own tick_interval_seconds), and runs each
due tick in a worker thread (run_tick does blocking DB + network I/O, so
it must not run on the event loop directly).

Design notes / honest limitations for Sprint 3:
- Single-process only. With multiple Uvicorn workers or replicas, each
  process would run its own scheduler and double-tick sessions. Fine for
  the current single-container Docker Compose deployment
  (14_Deployment_Guide.md Phase 1); a distributed lock (Redis is already
  in the stack) is the documented path when scaling out.
- Ticks for different sessions run sequentially within one poll cycle.
  Acceptable at Sprint 3 scale; parallelizing is future work.
- Each tick gets a FRESH database session (never a shared long-lived one),
  matching how request handlers work.
"""
from __future__ import annotations

import asyncio
import datetime as dt

from app.infrastructure.logging.logger import get_logger

logger = get_logger("paper_trading.scheduler")

POLL_INTERVAL_SECONDS = 5
"""How often the scheduler checks for due sessions. This is NOT the trading
tick rate — each session ticks at its own tick_interval_seconds; this is
just the granularity at which due-ness is evaluated."""


class PaperTradingScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="paper-trading-scheduler")
        logger.info("paper_trading.scheduler_started")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        try:
            await asyncio.wait_for(self._task, timeout=POLL_INTERVAL_SECONDS + 5)
        except asyncio.TimeoutError:  # pragma: no cover
            self._task.cancel()
        self._task = None
        logger.info("paper_trading.scheduler_stopped")

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                due_ids = await asyncio.to_thread(_find_due_session_ids)
                for session_id in due_ids:
                    if self._stopping.is_set():
                        break
                    await asyncio.to_thread(_tick_one, session_id)
            except Exception:  # noqa: BLE001 — the scheduler itself must never die
                logger.exception("paper_trading.scheduler_cycle_error")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass  # normal: poll interval elapsed, loop again


def find_due_session_ids(db, now: dt.datetime | None = None) -> list:
    """Sessions whose tick is due: status == running AND (never ticked OR
    tick_interval_seconds elapsed since last_tick_at). Takes `db` (and an
    injectable `now`) so the timing decision is directly unit-testable;
    the scheduler thread wrapper below supplies a real SessionLocal."""
    from app.infrastructure.repositories.paper_trading_repository import PaperTradingSessionRepository

    now = now or dt.datetime.now(dt.timezone.utc)
    due = []
    for session in PaperTradingSessionRepository(db).list_running():
        if session.last_tick_at is None:
            due.append(session.id)
            continue
        last = session.last_tick_at
        if last.tzinfo is None:  # SQLite can round-trip naive datetimes
            last = last.replace(tzinfo=dt.timezone.utc)
        if (now - last).total_seconds() >= session.tick_interval_seconds:
            due.append(session.id)
    return due


def _find_due_session_ids() -> list:
    """Runs in a worker thread; opens and closes its own DB session."""
    from app.infrastructure.database.session import SessionLocal

    db = SessionLocal()
    try:
        return find_due_session_ids(db)
    finally:
        db.close()


def _tick_one(session_id) -> None:
    """Runs in a worker thread; opens and closes its own DB session.
    A failing tick is logged and skipped — one bad session must never
    stall the scheduler or other sessions."""
    from app.application.services.paper_trading_service import PaperTradingService
    from app.infrastructure.database.session import SessionLocal

    db = SessionLocal()
    try:
        result = PaperTradingService(db).run_tick(session_id)
        if result.actions or result.rejections:
            logger.info(
                "paper_trading.tick",
                extra={
                    "session_id": str(session_id),
                    "actions": len(result.actions),
                    "rejections": len(result.rejections),
                    "data_feed_ok": result.data_feed_ok,
                },
            )
    except Exception:  # noqa: BLE001
        logger.exception("paper_trading.tick_failed", extra={"session_id": str(session_id)})
    finally:
        db.close()


# Module-level singleton used by main.py's lifespan handler.
scheduler = PaperTradingScheduler()
