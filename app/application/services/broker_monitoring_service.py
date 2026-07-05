"""
Broker monitoring wiring (Sprint 4 modules 9 + 10 integration): connects
a MonitoredBrokerAdapter's trip event to the EXISTING Sprint 1 kill
switch mechanism (RiskService.trigger_kill_switch -- already enforced
first in risk_engine.evaluate_order, already audited as a RiskEvent),
rather than inventing a second, parallel stop mechanism. One kill
switch, multiple triggers.

On trip, three things happen, in order:
1. RiskService.trigger_kill_switch(portfolio, reason) -- from this moment
   the Risk Engine rejects every new order in EVERY mode (live, paper,
   manual), and LiveExecutionEngine's own first gate rejects live
   submissions before they even reach the risk evaluation.
2. The broker account's last_connection_error is stamped with the trip
   reason (operator diagnostics without log-diving).
3. A CRITICAL notification is created for the user.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.application.services.risk_service import RiskService
from app.domain.broker.broker_interface import BrokerAdapter
from app.domain.broker.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from app.infrastructure.brokers.monitored_broker import MonitoredBrokerAdapter
from app.infrastructure.logging.logger import get_logger
from app.infrastructure.models.broker_account import BrokerAccount
from app.infrastructure.models.enums import NotificationSeverity, NotificationType
from app.infrastructure.models.notification import Notification
from app.infrastructure.models.portfolio import Portfolio

logger = get_logger("broker_monitoring")


def build_monitored_broker(
    db: Session,
    portfolio: Portfolio,
    broker_account: BrokerAccount,
    inner: BrokerAdapter,
    config: CircuitBreakerConfig | None = None,
) -> MonitoredBrokerAdapter:
    """Wrap a raw adapter so that a circuit-breaker trip automatically
    triggers the kill switch for the given portfolio. The returned object
    is a drop-in BrokerAdapter -- hand it to LiveExecutionEngine as-is."""

    def _on_trip(reason: str) -> None:
        logger.critical(
            "broker_monitoring.circuit_breaker_tripped",
            extra={
                "broker_name": inner.broker_name,
                "broker_account_id": str(broker_account.id),
                "portfolio_id": str(portfolio.id),
                "reason": reason,
            },
        )
        RiskService(db).trigger_kill_switch(portfolio, f"Circuit breaker ({inner.broker_name}): {reason}")
        broker_account.last_connection_error = reason
        broker_account.last_health_check_at = dt.datetime.now(dt.timezone.utc)
        db.add(
            Notification(
                user_id=portfolio.user_id,
                type=NotificationType.RISK.value,
                title="Circuit Breaker Tripped — Live Trading Halted",
                message=reason,
                severity=NotificationSeverity.CRITICAL.value,
            )
        )
        db.commit()

    return MonitoredBrokerAdapter(
        inner,
        breaker=CircuitBreaker(config=config or CircuitBreakerConfig()),
        on_trip=_on_trip,
    )
