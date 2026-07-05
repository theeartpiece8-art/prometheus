"""
Integration tests for the full Sprint 4 automatic-stop chain:

    broker failures -> CircuitBreaker trips -> on_trip fires ->
    RiskService.trigger_kill_switch -> Portfolio.kill_switch_active ->
    every trading mode (live, manual/paper via Risk Engine) rejects new
    orders -> closing positions still works.

This is the integration the Sprint 4 plan's success criteria mean by
"Kill switch functions correctly" for the AUTOMATIC path -- the manual
kill switch mechanism itself is Sprint 1 code, already tested there.
"""
import uuid
from decimal import Decimal

import pytest

from app.application.schemas.order import OrderCreateRequest
from app.application.services.broker_monitoring_service import build_monitored_broker
from app.application.services.live_execution_service import LiveExecutionEngine
from app.domain.broker.broker_models import BrokerConnectionError
from app.domain.broker.circuit_breaker import CircuitBreakerConfig
from app.infrastructure.brokers.mock_broker import MockBrokerAdapter
from app.infrastructure.models.broker_account import BrokerAccount
from app.infrastructure.models.portfolio import Portfolio


@pytest.fixture()
def live_setup(registered_user, db_session):
    """A connected, live-enabled broker account + portfolio + monitored
    broker with a hair-trigger breaker (1 failure trips), wired to the
    kill switch."""
    _, _, body = registered_user
    user_id = uuid.UUID(body["user"]["id"])
    portfolio = db_session.query(Portfolio).filter(Portfolio.user_id == user_id).first()
    account = BrokerAccount(
        user_id=user_id, broker_name="mock", account_type="demo", status="connected", live_trading_enabled=True,
    )
    db_session.add(account)
    db_session.commit()

    inner = MockBrokerAdapter(tick_price=Decimal("100"))
    inner.connect()
    monitored = build_monitored_broker(
        db_session, portfolio, account, inner, config=CircuitBreakerConfig(max_consecutive_failures=1),
    )
    return portfolio, account, inner, monitored, user_id


class TestAutomaticKillSwitchChain:
    def test_breaker_trip_activates_the_kill_switch(self, live_setup, db_session):
        portfolio, account, inner, monitored, _ = live_setup
        assert portfolio.kill_switch_active is False

        inner.tick_should_fail = True
        with pytest.raises(BrokerConnectionError):
            monitored.get_tick("AAPL")

        db_session.refresh(portfolio)
        assert portfolio.kill_switch_active is True
        assert account.last_connection_error is not None
        assert account.last_health_check_at is not None

    def test_trip_creates_a_critical_risk_event_and_notification(self, live_setup, registered_user, db_session):
        portfolio, _, inner, monitored, _ = live_setup
        client, headers, _ = registered_user

        inner.tick_should_fail = True
        with pytest.raises(BrokerConnectionError):
            monitored.get_tick("AAPL")

        events = client.get("/api/v1/risk/events", headers=headers).json()
        assert any(e["event_type"] == "kill_switch_activated" for e in events)
        assert any("circuit breaker" in (e["description"] or "").lower() for e in events)

        notifications = client.get("/api/v1/notifications", headers=headers).json()
        assert any("Circuit Breaker Tripped" in n["title"] for n in notifications)
        assert any(n["severity"] == "critical" for n in notifications)

    def test_after_trip_live_orders_are_rejected_at_the_first_gate(self, live_setup, db_session):
        portfolio, account, inner, monitored, user_id = live_setup
        engine = LiveExecutionEngine(db_session, monitored)

        inner.tick_should_fail = True
        with pytest.raises(BrokerConnectionError):
            monitored.get_tick("AAPL")
        inner.tick_should_fail = False
        db_session.refresh(portfolio)

        order = engine.submit_order(
            user_id, portfolio, account,
            OrderCreateRequest(symbol="AAPL", side="buy", order_type="market", stop_loss=Decimal("90")),
        )
        assert order.status == "rejected"
        assert "kill switch" in order.rejection_reason.lower()

    def test_after_trip_even_manual_simulated_orders_are_rejected_by_the_risk_engine(
        self, live_setup, registered_user, db_session
    ):
        """One kill switch, every mode: the same flag gates the ordinary
        /orders/place pipeline (manual + paper trading), not just live."""
        portfolio, _, inner, monitored, _ = live_setup
        client, headers, _ = registered_user

        inner.tick_should_fail = True
        with pytest.raises(BrokerConnectionError):
            monitored.get_tick("AAPL")

        r = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90},
            headers=headers,
        )
        assert r.json()["order"]["status"] == "rejected"
        assert "kill switch" in r.json()["order"]["rejection_reason"].lower()

    def test_after_trip_closing_the_live_position_still_works(self, live_setup, registered_user, db_session):
        """The end-to-end version of the wrapper unit test: with the kill
        switch active and the breaker open, the operator can still exit
        an existing live position through the full engine pipeline."""
        portfolio, account, inner, monitored, user_id = live_setup
        client, headers, _ = registered_user
        engine = LiveExecutionEngine(db_session, monitored)

        opened = engine.submit_order(
            user_id, portfolio, account,
            OrderCreateRequest(symbol="AAPL", side="buy", order_type="market", stop_loss=Decimal("90")),
        )
        assert opened.status == "filled"

        inner.tick_should_fail = True
        with pytest.raises(BrokerConnectionError):
            monitored.get_tick("AAPL")
        inner.tick_should_fail = False
        db_session.refresh(portfolio)
        assert portfolio.kill_switch_active is True

        position = client.get("/api/v1/positions", headers=headers).json()[0]
        close_order = engine.close_position(portfolio, account, uuid.UUID(position["id"]))
        assert close_order.status == "filled"
        assert client.get("/api/v1/positions", headers=headers).json() == []

    def test_business_rejections_never_trip_the_chain(self, live_setup, db_session):
        portfolio, account, inner, monitored, user_id = live_setup
        engine = LiveExecutionEngine(db_session, monitored)
        inner.reject_next_n_orders = 3

        for _ in range(3):
            order = engine.submit_order(
                user_id, portfolio, account,
                OrderCreateRequest(symbol="AAPL", side="buy", order_type="market", stop_loss=Decimal("90")),
            )
            assert order.status == "rejected"

        db_session.refresh(portfolio)
        assert portfolio.kill_switch_active is False
        assert monitored.breaker.is_open is False
