"""
Tests for the Paper Trading scheduler's decision logic (Sprint 3).

The asyncio loop itself is a thin wrapper; what's tested here is the part
that decides anything: find_due_session_ids (which sessions get a tick,
based on status + elapsed time) plus a start/stop smoke test of the
scheduler task lifecycle.
"""
import asyncio
import datetime as dt
import uuid

from app.infrastructure.models.paper_trading_session import PaperTradingSession
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.user import User
from app.infrastructure.scheduling.paper_trading_scheduler import (
    PaperTradingScheduler,
    find_due_session_ids,
)


def _make_session(db, status="running", tick_interval_seconds=60, last_tick_at=None):
    user = User(username=f"u{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@x.com", password_hash="x")
    db.add(user)
    db.flush()
    portfolio = Portfolio(user_id=user.id, name="Default", balance=10000, equity=10000, free_margin=10000)
    db.add(portfolio)
    db.flush()
    session = PaperTradingSession(
        portfolio_id=portfolio.id, user_id=user.id, status=status,
        tick_interval_seconds=tick_interval_seconds, last_tick_at=last_tick_at,
    )
    db.add(session)
    db.commit()
    return session


class TestFindDueSessionIds:
    def test_never_ticked_running_session_is_due(self, db_session):
        session = _make_session(db_session, last_tick_at=None)
        assert find_due_session_ids(db_session) == [session.id]

    def test_session_with_elapsed_interval_is_due(self, db_session):
        now = dt.datetime.now(dt.timezone.utc)
        session = _make_session(db_session, tick_interval_seconds=60, last_tick_at=now - dt.timedelta(seconds=61))
        assert find_due_session_ids(db_session, now=now) == [session.id]

    def test_session_ticked_recently_is_not_due(self, db_session):
        now = dt.datetime.now(dt.timezone.utc)
        _make_session(db_session, tick_interval_seconds=60, last_tick_at=now - dt.timedelta(seconds=30))
        assert find_due_session_ids(db_session, now=now) == []

    def test_boundary_exactly_at_interval_is_due(self, db_session):
        now = dt.datetime.now(dt.timezone.utc)
        session = _make_session(db_session, tick_interval_seconds=60, last_tick_at=now - dt.timedelta(seconds=60))
        assert find_due_session_ids(db_session, now=now) == [session.id]

    def test_paused_and_stopped_sessions_never_due(self, db_session):
        _make_session(db_session, status="paused", last_tick_at=None)
        _make_session(db_session, status="stopped", last_tick_at=None)
        _make_session(db_session, status="interrupted", last_tick_at=None)
        assert find_due_session_ids(db_session) == []

    def test_each_session_uses_its_own_interval(self, db_session):
        now = dt.datetime.now(dt.timezone.utc)
        fast = _make_session(db_session, tick_interval_seconds=15, last_tick_at=now - dt.timedelta(seconds=20))
        _make_session(db_session, tick_interval_seconds=300, last_tick_at=now - dt.timedelta(seconds=20))
        assert find_due_session_ids(db_session, now=now) == [fast.id]


class TestSchedulerLifecycle:
    def test_start_and_stop_cleanly(self, db_session, monkeypatch):
        """Smoke test: the scheduler task starts, runs at least one poll
        cycle against a patched (no real DB) due-check, and stops within
        the shutdown timeout without hanging or raising."""
        calls = {"count": 0}

        def _fake_find():
            calls["count"] += 1
            return []

        monkeypatch.setattr(
            "app.infrastructure.scheduling.paper_trading_scheduler._find_due_session_ids", _fake_find
        )

        async def _run():
            scheduler = PaperTradingScheduler()
            await scheduler.start()
            assert scheduler._task is not None
            await asyncio.sleep(0.1)  # let at least one poll cycle run
            await scheduler.stop()
            assert scheduler._task is None

        asyncio.run(_run())
        assert calls["count"] >= 1

    def test_double_start_is_idempotent(self, monkeypatch):
        monkeypatch.setattr(
            "app.infrastructure.scheduling.paper_trading_scheduler._find_due_session_ids", lambda: []
        )

        async def _run():
            scheduler = PaperTradingScheduler()
            await scheduler.start()
            first_task = scheduler._task
            await scheduler.start()  # second start must not spawn a new task
            assert scheduler._task is first_task
            await scheduler.stop()

        asyncio.run(_run())
