"""
Risk service: the Application-layer bridge between the pure Domain
RiskEngine and the database. This is the ONLY place in the codebase that
is allowed to construct AccountState/RiskSettings from persisted data and
call RiskEngine.evaluate_order — OrderService always goes through here,
never calls the domain engine directly. This keeps the "never bypass the
Risk Engine" invariant enforceable at a single choke point.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.decimal_utils import clean_decimal
from app.domain.risk.risk_engine import risk_engine
from app.domain.risk.risk_models import AccountState, OrderRequest, RiskDecision, RiskSettings
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.models.enums import RiskEventSeverity
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.risk_event import RiskEvent
from app.infrastructure.repositories.position_repository import PositionRepository
from app.infrastructure.repositories.settings_repository import SettingsRepository
from app.infrastructure.repositories.trade_repository import TradeRepository

logger = get_logger("risk")


def _start_of_today_utc() -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


class RiskService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.positions = PositionRepository(db)
        self.trades = TradeRepository(db)
        self.settings_repo = SettingsRepository(db)

    def load_risk_settings(self, user_id: uuid.UUID) -> RiskSettings:
        row = self.settings_repo.get_for_user(user_id)
        if row is None or not row.risk_settings:
            return RiskSettings()
        rs = row.risk_settings
        return RiskSettings(
            risk_per_trade_pct=Decimal(str(rs.get("risk_per_trade_pct", 1.0))),
            max_daily_loss_pct=Decimal(str(rs.get("max_daily_loss_pct", 3.0))),
            max_drawdown_pct=Decimal(str(rs.get("max_drawdown_pct", 10.0))),
            max_open_positions=int(rs.get("max_open_positions", 10)),
            max_positions_per_symbol=int(rs.get("max_positions_per_symbol", 2)),
            max_portfolio_exposure_pct=Decimal(str(rs.get("max_portfolio_exposure_pct", 50.0))),
            max_symbol_exposure_pct=Decimal(str(rs.get("max_symbol_exposure_pct", 20.0))),
            max_leverage=Decimal(str(rs.get("max_leverage", 10.0))),
            min_account_balance=Decimal(str(rs.get("min_account_balance", 0))),
            allowed_symbols=rs.get("allowed_symbols"),
        )

    def build_account_state(self, portfolio: Portfolio, symbol: str) -> AccountState:
        drawdown_pct = Decimal("0")
        if portfolio.peak_equity > 0:
            drawdown_pct = clean_decimal(
                max(Decimal("0"), (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity * 100)
            )

        daily_loss = self.trades.realized_loss_since(portfolio.id, _start_of_today_utc())
        # Unrealized losses on currently-open positions count against the daily
        # loss budget too (07_Risk_Management_Engine.md: "Monitor realized and
        # unrealized losses").
        if portfolio.unrealized_pnl < 0:
            daily_loss += abs(portfolio.unrealized_pnl)

        return AccountState(
            equity=portfolio.equity,
            balance=portfolio.balance,
            open_positions_count=self.positions.count_open_for_portfolio(portfolio.id),
            positions_for_symbol_count=self.positions.count_open_for_symbol(portfolio.id, symbol),
            current_daily_loss=daily_loss,
            current_drawdown_pct=drawdown_pct,
            current_exposure_by_symbol=self.positions.exposure_by_symbol(portfolio.id),
            current_portfolio_exposure=self.positions.total_exposure(portfolio.id),
            kill_switch_active=portfolio.kill_switch_active,
        )

    def evaluate(
        self, portfolio: Portfolio, user_id: uuid.UUID, order_request: OrderRequest
    ) -> RiskDecision:
        account_state = self.build_account_state(portfolio, order_request.symbol)
        risk_settings = self.load_risk_settings(user_id)
        decision = risk_engine.evaluate_order(order_request, account_state, risk_settings)

        if decision.approved:
            logger.info(
                "risk.order_approved",
                extra={"portfolio_id": str(portfolio.id), "symbol": order_request.symbol,
                       "approved_size": str(decision.approved_position_size)},
            )
        else:
            logger.warning(
                "risk.order_rejected",
                extra={"portfolio_id": str(portfolio.id), "symbol": order_request.symbol, "reason": decision.reason},
            )
            self._record_risk_event(portfolio.id, order_request.symbol, decision)

        return decision

    def _record_risk_event(self, portfolio_id: uuid.UUID, symbol: str, decision: RiskDecision) -> None:
        failed_rule = next((c.rule for c in decision.checks if c.result.value == "fail"), "unknown")
        event = RiskEvent(
            portfolio_id=portfolio_id,
            event_type=f"order_rejected.{failed_rule}",
            description=f"Order for {symbol} rejected: {decision.reason}",
            severity=RiskEventSeverity.MEDIUM.value,
            action_taken="order_rejected",
        )
        self.db.add(event)
        self.db.flush()

    def trigger_kill_switch(self, portfolio: Portfolio, reason: str) -> None:
        portfolio.kill_switch_active = True
        event = RiskEvent(
            portfolio_id=portfolio.id,
            event_type="kill_switch_activated",
            description=reason,
            severity=RiskEventSeverity.CRITICAL.value,
            action_taken="kill_switch_activated",
        )
        self.db.add(event)
        self.db.commit()
        logger.critical("risk.kill_switch_activated", extra={"portfolio_id": str(portfolio.id), "reason": reason})

    def reset_kill_switch(self, portfolio: Portfolio) -> None:
        portfolio.kill_switch_active = False
        event = RiskEvent(
            portfolio_id=portfolio.id,
            event_type="kill_switch_reset",
            description="Kill switch manually reset by user.",
            severity=RiskEventSeverity.HIGH.value,
            action_taken="kill_switch_reset",
        )
        self.db.add(event)
        self.db.commit()
        logger.warning("risk.kill_switch_reset", extra={"portfolio_id": str(portfolio.id)})
