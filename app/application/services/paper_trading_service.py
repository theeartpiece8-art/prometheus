"""
Paper Trading service, per 09_Paper_Trading_Engine.md.

Central design decision (opposite of Sprint 2's Backtesting Engine, and
documented at length in the README's Sprint 3 section): this service
DIRECTLY reuses `OrderService.create_order()` and `OrderService.
close_position()` — the exact same pipeline manual orders already use —
rather than reimplementing position/order logic. This is correct here
(unlike backtesting) because paper trading operates on LIVE current data
and must "behave exactly as they would in live trading" (spec text). The
only genuinely new logic in this file is: (1) session lifecycle/validation,
and (2) the loop that decides *when* to call the existing order pipeline.

`run_tick()` is the one method that matters most: it is a plain, directly
callable, synchronously-testable method (no async, no sleeping) that
evaluates one tick for one session. The actual continuous background
execution is a thin asyncio wrapper around it — see
app/infrastructure/scheduling/paper_trading_scheduler.py.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.application.schemas.order import OrderCreateRequest
from app.application.schemas.paper_trading import (
    StartSessionRequest,
    TickActionSummary,
    TickRejectionSummary,
    TickResultResponse,
)
from app.application.services.order_service import OrderService, OrderServiceError, StrategyNotFoundError
from app.application.services.risk_service import RiskService
from app.domain.strategy.sample_strategies import instantiate_strategy
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.market_data.base_provider import MarketDataProvider, MarketDataProviderError
from app.infrastructure.market_data.provider import default_provider, list_supported_symbols
from app.infrastructure.market_data.utils import fetch_bars_with_source
from app.infrastructure.models.enums import NotificationSeverity, NotificationType
from app.infrastructure.models.notification import Notification
from app.infrastructure.models.paper_trading_session import PaperTradingSession, PaperTradingSessionItem
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.strategy import Strategy
from app.infrastructure.repositories.paper_trading_repository import PaperTradingSessionRepository
from app.infrastructure.repositories.portfolio_repository import PortfolioRepository
from app.infrastructure.repositories.position_repository import PositionRepository
from app.infrastructure.repositories.strategy_repository import StrategyRepository

logger = get_logger("paper_trading")

STRATEGY_LOOKBACK_DAYS = 120
"""How much historical context to fetch each tick so the strategy has
enough bars to evaluate (covers the default slow_period=30 comfortably,
even accounting for weekends/holidays on daily bars). A fully general
implementation would size this from the strategy's own parameters and
timeframe granularity -- flagged as a reasonable future refinement, not
core to Sprint 3."""


class PaperTradingServiceError(Exception):
    pass


class SessionValidationError(PaperTradingServiceError):
    pass


class SessionNotFoundError(PaperTradingServiceError):
    pass


class InvalidSessionStateError(PaperTradingServiceError):
    pass


class ResetNotAllowedError(PaperTradingServiceError):
    pass


def mark_interrupted_sessions(db: Session) -> int:
    """09_Paper_Trading_Engine.md, Testing Requirements: 'Session recovery
    after interruption'. A session left status='running' at app startup
    means the previous process died without a clean stop. We deliberately
    do NOT silently auto-resume trading after an outage of unknown length —
    the session is marked 'interrupted' with a reason and the user restarts
    explicitly (same do-not-quietly-continue philosophy as the Risk
    Engine's kill switch). Returns the number of sessions marked. Called
    from main.py's lifespan handler; takes `db` as a parameter so the
    behavior is directly testable."""
    orphans = PaperTradingSessionRepository(db).list_running()
    for session in orphans:
        session.status = "interrupted"
        session.stopped_at = dt.datetime.now(dt.timezone.utc)
        session.status_reason = (
            "Application restarted while this session was running. "
            "Start a new session to resume paper trading."
        )
    if orphans:
        db.commit()
        logger.warning("paper_trading.sessions_marked_interrupted", extra={"count": len(orphans)})
    return len(orphans)


class PaperTradingService:
    def __init__(self, db: Session, market_data_provider: MarketDataProvider | None = None) -> None:
        self.db = db
        self.sessions = PaperTradingSessionRepository(db)
        self.portfolios = PortfolioRepository(db)
        self.positions = PositionRepository(db)
        self.strategies = StrategyRepository(db)
        self.order_service = OrderService(db)
        self.risk_service = RiskService(db)
        self._provider = market_data_provider or default_provider

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(self, user_id: uuid.UUID, request: StartSessionRequest) -> PaperTradingSession:
        """Implements 09_Paper_Trading_Engine.md's "Validation Rules":
        reject startup (no session row created at all) if anything fails."""
        errors: list[str] = []

        portfolio = self.portfolios.get_default_for_user(user_id)
        if portfolio is None:
            errors.append("Portfolio is not initialized.")

        try:
            self.risk_service.load_risk_settings(user_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Risk settings could not be loaded: {exc}")

        supported_symbols = {s["symbol"] for s in list_supported_symbols()}
        for item in request.items:
            strategy = self.strategies.get_for_user(item.strategy_id, user_id)
            if strategy is None:
                errors.append(f"Strategy {item.strategy_id} not found.")
                continue
            if strategy.status != "active":
                errors.append(f"Strategy '{strategy.name}' is not enabled (status='{strategy.status}').")
            if item.symbol not in supported_symbols:
                errors.append(f"Symbol '{item.symbol}' is not in the supported instruments list.")
            try:
                self._provider.get_latest_price(item.symbol)
            except MarketDataProviderError as exc:
                # NOTE: with the default FallbackMarketDataProvider, this
                # practically never fires -- the mock fallback never raises.
                # It's a real, meaningful check against an injected
                # provider (e.g. in tests) or a future provider without a
                # fallback. Documented rather than silently vacuous.
                errors.append(f"Live market data unavailable for '{item.symbol}': {exc}")

        if errors:
            raise SessionValidationError(" | ".join(errors))

        session = PaperTradingSession(
            portfolio_id=portfolio.id, user_id=user_id, status="running",
            tick_interval_seconds=request.tick_interval_seconds, started_at=dt.datetime.now(dt.timezone.utc),
        )
        self.db.add(session)
        self.db.flush()
        for item in request.items:
            self.db.add(
                PaperTradingSessionItem(
                    session_id=session.id, strategy_id=item.strategy_id, symbol=item.symbol, timeframe=item.timeframe
                )
            )
        self.db.commit()
        self.db.refresh(session)
        logger.info(
            "paper_trading.session_started", extra={"session_id": str(session.id), "items": len(request.items)}
        )
        return session

    def get_for_user(self, session_id: uuid.UUID, user_id: uuid.UUID) -> PaperTradingSession:
        session = self.sessions.get_for_user(session_id, user_id)
        if session is None:
            raise SessionNotFoundError("Paper trading session not found.")
        return session

    def list_for_user(self, user_id: uuid.UUID) -> list[PaperTradingSession]:
        return self.sessions.list_for_user(user_id)

    def pause_session(self, session_id: uuid.UUID, user_id: uuid.UUID) -> PaperTradingSession:
        session = self.get_for_user(session_id, user_id)
        if session.status != "running":
            raise InvalidSessionStateError(f"Cannot pause a session with status '{session.status}'.")
        session.status = "paused"
        session.paused_at = dt.datetime.now(dt.timezone.utc)
        self._notify_session_event(user_id, "Strategy Paused", "Paper trading session paused.")
        self.db.commit()
        self.db.refresh(session)
        return session

    def resume_session(self, session_id: uuid.UUID, user_id: uuid.UUID) -> PaperTradingSession:
        session = self.get_for_user(session_id, user_id)
        if session.status != "paused":
            raise InvalidSessionStateError(f"Cannot resume a session with status '{session.status}'.")
        session.status = "running"
        session.paused_at = None
        self.db.commit()
        self.db.refresh(session)
        return session

    def stop_session(self, session_id: uuid.UUID, user_id: uuid.UUID) -> PaperTradingSession:
        session = self.get_for_user(session_id, user_id)
        if session.status not in ("running", "paused"):
            raise InvalidSessionStateError(f"Cannot stop a session with status '{session.status}'.")
        session.status = "stopped"
        session.stopped_at = dt.datetime.now(dt.timezone.utc)
        self.db.commit()
        self.db.refresh(session)
        return session

    def reset_paper_account(self, user_id: uuid.UUID, starting_balance: Decimal) -> Portfolio:
        """Only allowed with no active sessions and no open positions --
        resetting mid-session or with real exposure open would silently
        discard live risk state, which this platform treats as unsafe by
        design (same philosophy as the Risk Engine's kill switch)."""
        portfolio = self.portfolios.get_default_for_user(user_id)
        if portfolio is None:
            raise PaperTradingServiceError("No portfolio found for this user.")

        active = [s for s in self.sessions.list_for_user(user_id) if s.status in ("running", "paused")]
        if active:
            raise ResetNotAllowedError("Stop all paper trading sessions before resetting the account.")

        open_positions = self.positions.list_open_for_portfolio(portfolio.id)
        if open_positions:
            raise ResetNotAllowedError("Close all open positions before resetting the account.")

        portfolio.balance = starting_balance
        portfolio.equity = starting_balance
        portfolio.free_margin = starting_balance
        portfolio.margin_used = Decimal("0")
        portfolio.unrealized_pnl = Decimal("0")
        portfolio.realized_pnl = Decimal("0")
        portfolio.peak_equity = starting_balance
        portfolio.kill_switch_active = False
        self.db.commit()
        self.db.refresh(portfolio)
        logger.info("paper_trading.account_reset", extra={"portfolio_id": str(portfolio.id)})
        return portfolio

    # ------------------------------------------------------------------
    # The tick loop
    # ------------------------------------------------------------------

    def run_tick(self, session_id: uuid.UUID) -> TickResultResponse:
        session = self.sessions.get(session_id)
        if session is None or session.status != "running":
            # Race-safe no-op: the scheduler may fire a tick for a session
            # that was paused/stopped microseconds earlier.
            return TickResultResponse(
                session_id=session_id, ticked_at=dt.datetime.now(dt.timezone.utc),
                items_evaluated=0, actions=[], rejections=[], data_feed_ok=True,
            )

        portfolio = self.db.get(Portfolio, session.portfolio_id)
        actions: list[TickActionSummary] = []
        rejections: list[TickRejectionSummary] = []
        data_feed_ok = True

        item_by_symbol: dict[str, PaperTradingSessionItem] = {item.symbol: item for item in session.items}

        # 1. Monitor existing open positions for stop-loss / take-profit hits.
        for symbol in item_by_symbol:
            position = self.positions.get_open_for_symbol(portfolio.id, symbol)
            if position is None:
                continue
            try:
                price, _source = self._get_latest_price_with_source(symbol)
            except Exception as exc:  # noqa: BLE001
                data_feed_ok = False
                logger.warning("paper_trading.data_feed_error", extra={"symbol": symbol, "error": str(exc)})
                continue

            hit_reason = self._check_sl_tp(position, price)
            if hit_reason is not None:
                try:
                    self.order_service.close_position(
                        portfolio, position.id, reason=hit_reason,
                        strategy_id=item_by_symbol[symbol].strategy_id,
                    )
                    action = "closed_stop_loss" if hit_reason == "Stop Loss Hit" else "closed_take_profit"
                    actions.append(
                        TickActionSummary(
                            strategy_id=item_by_symbol[symbol].strategy_id, symbol=symbol, action=action
                        )
                    )
                except OrderServiceError as exc:  # extremely unlikely (position just checked open); never abort the tick
                    logger.warning("paper_trading.sl_tp_close_failed", extra={"symbol": symbol, "error": str(exc)})

        # 2. Evaluate each tracked (strategy, symbol) for new signals.
        for item in session.items:
            strategy_row = self.db.get(Strategy, item.strategy_id)
            if strategy_row is None or strategy_row.status != "active":
                rejections.append(
                    TickRejectionSummary(
                        strategy_id=item.strategy_id, symbol=item.symbol, reason="Strategy is not active."
                    )
                )
                continue

            try:
                strategy_instance, _ = instantiate_strategy(strategy_row.parameters)
            except ValueError as exc:
                rejections.append(
                    TickRejectionSummary(strategy_id=item.strategy_id, symbol=item.symbol, reason=str(exc))
                )
                continue

            end = dt.datetime.now(dt.timezone.utc)
            start = end - dt.timedelta(days=STRATEGY_LOOKBACK_DAYS)
            try:
                raw_bars, _source = fetch_bars_with_source(self._provider, item.symbol, item.timeframe, start, end)
            except MarketDataProviderError as exc:
                data_feed_ok = False
                rejections.append(
                    TickRejectionSummary(
                        strategy_id=item.strategy_id, symbol=item.symbol, reason=f"Data feed error: {exc}"
                    )
                )
                continue

            bars = [_to_domain_bar(b) for b in raw_bars]
            signal = strategy_instance.generate_signal(item.symbol, bars)
            if signal is None or signal.signal_type.value == "hold":
                continue

            order_request = OrderCreateRequest(
                symbol=item.symbol, side=signal.signal_type.value, order_type="market",
                stop_loss=signal.suggested_stop_loss, take_profit=signal.suggested_take_profit,
                quantity=None, strategy_id=item.strategy_id,
            )
            try:
                order, _checks, _source = self.order_service.create_order(session.user_id, portfolio, order_request)
            except StrategyNotFoundError as exc:  # pragma: no cover -- strategy existence already checked above
                rejections.append(
                    TickRejectionSummary(strategy_id=item.strategy_id, symbol=item.symbol, reason=str(exc))
                )
                continue

            if order.status == "filled":
                actions.append(
                    TickActionSummary(
                        strategy_id=item.strategy_id, symbol=item.symbol, action="opened", order_id=order.id
                    )
                )
            else:
                rejections.append(
                    TickRejectionSummary(
                        strategy_id=item.strategy_id, symbol=item.symbol,
                        reason=order.rejection_reason or "rejected",
                    )
                )

        session.last_tick_at = dt.datetime.now(dt.timezone.utc)
        session.tick_count += 1
        if not data_feed_ok:
            self._notify_session_event(
                session.user_id, "Data Feed Interrupted",
                "One or more symbols could not be fetched during this paper trading tick.",
                severity=NotificationSeverity.WARNING,
            )
        self.db.commit()

        return TickResultResponse(
            session_id=session.id, ticked_at=session.last_tick_at, items_evaluated=len(session.items),
            actions=actions, rejections=rejections, data_feed_ok=data_feed_ok,
        )

    # ------------------------------------------------------------------
    # Strategy monitoring (09_Paper_Trading_Engine.md "Strategy Monitoring")
    # ------------------------------------------------------------------

    def strategy_monitor(self, session_id: uuid.UUID, user_id: uuid.UUID) -> list[dict]:
        """Per-strategy stats for a session, computed from the REAL trades
        table (the same rows the analytics endpoints read) — not a separate
        parallel bookkeeping. current_drawdown_pct is the portfolio-level
        drawdown: per-strategy equity curves aren't tracked (all strategies
        share the one portfolio by design), so the portfolio number is the
        honest available figure rather than a fabricated per-strategy one."""
        from app.infrastructure.repositories.trade_repository import TradeRepository

        session = self.get_for_user(session_id, user_id)
        portfolio = self.db.get(Portfolio, session.portfolio_id)
        trades_repo = TradeRepository(self.db)

        drawdown_pct = 0.0
        if portfolio.peak_equity and portfolio.peak_equity > 0:
            drawdown_pct = float(
                max(Decimal("0"), (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity * 100)
            )

        monitors: list[dict] = []
        for item in session.items:
            strategy_row = self.db.get(Strategy, item.strategy_id)
            trades = trades_repo.list_for_strategy(item.strategy_id, limit=1000)
            wins = [t for t in trades if t.net_profit is not None and t.net_profit > 0]
            losses = [t for t in trades if t.net_profit is not None and t.net_profit < 0]
            gross_profit = sum((t.net_profit for t in wins), Decimal("0"))
            gross_loss = abs(sum((t.net_profit for t in losses), Decimal("0")))
            running_pnl = sum((t.net_profit for t in trades if t.net_profit is not None), Decimal("0"))

            position = self.positions.get_open_for_symbol(portfolio.id, item.symbol)

            monitors.append(
                {
                    "strategy_id": item.strategy_id,
                    "strategy_name": strategy_row.name if strategy_row else "(deleted)",
                    "symbol": item.symbol,
                    "status": session.status,
                    "current_position": position.direction if position else None,
                    "number_of_trades": len(trades),
                    "win_rate": (len(wins) / len(trades) * 100) if trades else None,
                    "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else None,
                    "current_drawdown_pct": drawdown_pct,
                    "running_pnl": float(running_pnl),
                }
            )
        return monitors

    # ------------------------------------------------------------------

    def _get_latest_price_with_source(self, symbol: str) -> tuple[Decimal, str]:
        """Mirror of fetch_bars_with_source for single prices: use the
        richer with-source API when the provider offers it, otherwise fall
        back to the plain interface."""
        from app.infrastructure.market_data.fallback_provider import FallbackMarketDataProvider

        if isinstance(self._provider, FallbackMarketDataProvider):
            return self._provider.get_latest_price_with_source(symbol)
        return self._provider.get_latest_price(symbol), getattr(self._provider, "name", "unknown")

    def _check_sl_tp(self, position, price: Decimal) -> str | None:
        if position.direction == "long":
            if position.stop_loss is not None and price <= position.stop_loss:
                return "Stop Loss Hit"
            if position.take_profit is not None and price >= position.take_profit:
                return "Take Profit Hit"
        else:
            if position.stop_loss is not None and price >= position.stop_loss:
                return "Stop Loss Hit"
            if position.take_profit is not None and price <= position.take_profit:
                return "Take Profit Hit"
        return None

    def _notify_session_event(
        self, user_id: uuid.UUID, title: str, message: str,
        severity: NotificationSeverity = NotificationSeverity.INFO,
    ) -> None:
        self.db.add(
            Notification(
                user_id=user_id, type=NotificationType.SYSTEM.value,
                title=title, message=message, severity=severity.value,
            )
        )


def _to_domain_bar(raw: dict):
    from app.domain.strategy.base_strategy import Bar

    return Bar(
        timestamp=raw["timestamp"],
        open=Decimal(str(raw["open"])),
        high=Decimal(str(raw["high"])),
        low=Decimal(str(raw["low"])),
        close=Decimal(str(raw["close"])),
        volume=Decimal(str(raw["volume"])),
    )
