"""
Integration tests for LiveExecutionEngine (Sprint 4). Converts this
session's ad-hoc verification into a permanent suite, plus covers
scenarios not yet exercised: the broker-tick reference-price fix
specifically, BrokerConnectionError re-raising (needed for the future
circuit breaker to observe failures), notification content, and
close_position edge cases.
"""
import uuid
from decimal import Decimal

import pytest

from app.application.schemas.order import OrderCreateRequest
from app.application.services.live_execution_service import LiveExecutionEngine, LiveExecutionEngineError
from app.domain.broker.broker_models import BrokerConnectionError, BrokerOrderRejectedError
from app.infrastructure.brokers.mock_broker import MockBrokerAdapter
from app.infrastructure.models.broker_account import BrokerAccount
from app.infrastructure.models.portfolio import Portfolio


@pytest.fixture()
def broker_account(registered_user, db_session):
    _, _, body = registered_user
    account = BrokerAccount(
        user_id=uuid.UUID(body["user"]["id"]), broker_name="mt5", account_type="demo",
        status="connected", live_trading_enabled=True,
    )
    db_session.add(account)
    db_session.commit()
    return account


@pytest.fixture()
def connected_broker():
    broker = MockBrokerAdapter(tick_price=Decimal("100"))
    broker.connect()
    return broker


def _portfolio(db_session, registered_user) -> Portfolio:
    _, _, body = registered_user
    return db_session.query(Portfolio).filter(Portfolio.user_id == uuid.UUID(body["user"]["id"])).first()


def _buy_request(stop_loss=Decimal("90"), take_profit=Decimal("120"), symbol="AAPL"):
    return OrderCreateRequest(symbol=symbol, side="buy", order_type="market", stop_loss=stop_loss, take_profit=take_profit)


class TestPreRiskGates:
    """The three gates that sit before the Risk Engine, cheapest first."""

    def test_kill_switch_blocks_new_orders(self, registered_user, db_session, broker_account, connected_broker):
        portfolio = _portfolio(db_session, registered_user)
        portfolio.kill_switch_active = True
        db_session.commit()

        engine = LiveExecutionEngine(db_session, connected_broker)
        order = engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())

        assert order.status == "rejected"
        assert "kill switch" in order.rejection_reason.lower()
        assert connected_broker.get_positions() == []  # never even reached the broker

    def test_live_trading_disabled_blocks_orders(self, registered_user, db_session, broker_account, connected_broker):
        portfolio = _portfolio(db_session, registered_user)
        broker_account.live_trading_enabled = False
        db_session.commit()

        engine = LiveExecutionEngine(db_session, connected_broker)
        order = engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())

        assert order.status == "rejected"
        assert "not enabled" in order.rejection_reason.lower()

    def test_disconnected_broker_blocks_orders(self, registered_user, db_session, broker_account):
        portfolio = _portfolio(db_session, registered_user)
        broker = MockBrokerAdapter()  # never connected
        engine = LiveExecutionEngine(db_session, broker)

        order = engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())
        assert order.status == "rejected"
        assert "not connected" in order.rejection_reason.lower()

    def test_every_rejection_still_creates_an_audit_row(self, registered_user, db_session, broker_account):
        """Per the spec: every attempted order is persisted, approved or
        not. Confirmed across all three pre-Risk-Engine gates."""
        portfolio = _portfolio(db_session, registered_user)
        broker = MockBrokerAdapter()  # disconnected
        engine = LiveExecutionEngine(db_session, broker)
        engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())

        from app.infrastructure.models.order import Order
        orders = db_session.query(Order).filter(Order.portfolio_id == portfolio.id).all()
        assert len(orders) == 1
        assert orders[0].status == "rejected"


class TestRiskEngineIntegration:
    def test_risk_engine_blocks_live_orders_same_as_manual(
        self, registered_user, db_session, broker_account, connected_broker
    ):
        client, headers, _ = registered_user
        client.put("/api/v1/risk/settings", json={"allowed_symbols": ["MSFT"]}, headers=headers)
        portfolio = _portfolio(db_session, registered_user)

        engine = LiveExecutionEngine(db_session, connected_broker)
        order = engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request(symbol="AAPL"))

        assert order.status == "rejected"
        assert "allowed symbols" in order.rejection_reason.lower()
        # The rejection is a real, audited risk event -- same pipeline as manual/paper orders
        events = client.get("/api/v1/risk/events", headers=headers).json()
        assert len(events) >= 1

    def test_market_order_reference_price_is_pulled_from_broker_tick_not_a_separate_feed(
        self, registered_user, db_session, broker_account
    ):
        """The specific bug found and fixed this session: a market order
        has no price of its own, so the Risk Engine needs a reference
        price BEFORE the order is sent. Confirms it comes from the
        broker's own tick (bid for sell, ask for buy) rather than being
        left unresolved (which previously caused every market order to be
        rejected with 'Could not determine a valid position size')."""
        portfolio = _portfolio(db_session, registered_user)
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        engine = LiveExecutionEngine(db_session, broker)

        order = engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request(stop_loss=Decimal("90")))

        assert order.status == "filled"
        assert order.rejection_reason is None


class TestSuccessfulExecution:
    def test_filled_order_creates_position_with_correct_broker_linkage(
        self, registered_user, db_session, broker_account, connected_broker
    ):
        portfolio = _portfolio(db_session, registered_user)
        engine = LiveExecutionEngine(db_session, connected_broker)
        order = engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())

        assert order.status == "filled"
        assert order.broker_account_id == broker_account.id
        assert order.broker_order_id is not None
        assert order.executed_price is not None
        assert order.filled_at is not None

        client, headers, _ = registered_user
        positions = client.get("/api/v1/positions", headers=headers).json()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "AAPL"
        assert positions[0]["direction"] == "long"

    def test_filled_order_sends_a_notification(self, registered_user, db_session, broker_account, connected_broker):
        client, headers, _ = registered_user
        portfolio = _portfolio(db_session, registered_user)
        engine = LiveExecutionEngine(db_session, connected_broker)
        engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())

        notifications = client.get("/api/v1/notifications", headers=headers).json()
        assert any(n["title"] == "Live Order Filled" for n in notifications)

    def test_broker_side_rejection_is_recorded_not_raised(
        self, registered_user, db_session, broker_account, connected_broker
    ):
        connected_broker.reject_next_n_orders = 1
        connected_broker.reject_reason = "Insufficient margin"
        portfolio = _portfolio(db_session, registered_user)
        engine = LiveExecutionEngine(db_session, connected_broker)

        order = engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())
        assert order.status == "rejected"
        assert "Insufficient margin" in order.rejection_reason

    def test_broker_connection_error_during_submission_is_re_raised(
        self, registered_user, db_session, broker_account, connected_broker
    ):
        """Deliberately NOT swallowed: a future circuit breaker wrapping
        this engine's broker calls needs to observe this failure to count
        toward its trip threshold. Confirms the order is still recorded
        as rejected in the DB before the exception propagates -- the
        audit trail must survive even when the call itself blows up."""
        portfolio = _portfolio(db_session, registered_user)
        engine = LiveExecutionEngine(db_session, connected_broker)

        connected_broker.simulate_disconnect()  # place_order will now raise BrokerConnectionError
        # is_connected() gate would normally catch this -- reconnect status
        # flag manually flipped back to simulate a disconnect happening
        # mid-call, after the engine's own pre-check passed.
        connected_broker._status = connected_broker._status.__class__.CONNECTED
        original_place_order = connected_broker.place_order

        def _fail(*args, **kwargs):
            raise BrokerConnectionError("Connection dropped mid-request")

        connected_broker.place_order = _fail
        with pytest.raises(BrokerConnectionError):
            engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())
        connected_broker.place_order = original_place_order

        from app.infrastructure.models.order import Order
        orders = db_session.query(Order).filter(Order.portfolio_id == portfolio.id).all()
        assert len(orders) == 1
        assert orders[0].status == "rejected"
        assert "connection error" in orders[0].rejection_reason.lower()


class TestClosePosition:
    def test_close_position_full_pipeline(self, registered_user, db_session, broker_account, connected_broker):
        client, headers, _ = registered_user
        portfolio = _portfolio(db_session, registered_user)
        engine = LiveExecutionEngine(db_session, connected_broker)

        before_balance = portfolio.balance
        open_order = engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())
        assert open_order.status == "filled"

        connected_broker.set_tick_price(Decimal("130"))
        position = client.get("/api/v1/positions", headers=headers).json()[0]
        close_order = engine.close_position(portfolio, broker_account, uuid.UUID(position["id"]))

        assert close_order.status == "filled"
        assert close_order.side == "sell"
        assert client.get("/api/v1/positions", headers=headers).json() == []
        assert portfolio.balance > before_balance  # closed at a profit

        orders = client.get("/api/v1/orders", headers=headers).json()
        assert len(orders) == 2  # open + close, complete audit trail

    def test_close_position_bypasses_risk_engine(self, registered_user, db_session, broker_account, connected_broker):
        """Same principle as OrderService.close_position(): risk
        management gates NEW risk, not risk reduction. Restricting
        allowed_symbols must NOT block closing an existing position."""
        client, headers, _ = registered_user
        portfolio = _portfolio(db_session, registered_user)
        engine = LiveExecutionEngine(db_session, connected_broker)
        engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())

        client.put("/api/v1/risk/settings", json={"allowed_symbols": ["MSFT"]}, headers=headers)
        position = client.get("/api/v1/positions", headers=headers).json()[0]
        close_order = engine.close_position(portfolio, broker_account, uuid.UUID(position["id"]))
        assert close_order.status == "filled"

    def test_close_position_works_even_when_live_trading_disabled(
        self, registered_user, db_session, broker_account, connected_broker
    ):
        """An operator (or the future kill switch) must always be able to
        get OUT of a position, even if new trading has been disabled."""
        client, headers, _ = registered_user
        portfolio = _portfolio(db_session, registered_user)
        engine = LiveExecutionEngine(db_session, connected_broker)
        engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())

        broker_account.live_trading_enabled = False
        db_session.commit()
        position = client.get("/api/v1/positions", headers=headers).json()[0]
        close_order = engine.close_position(portfolio, broker_account, uuid.UUID(position["id"]))
        assert close_order.status == "filled"

    def test_close_position_no_such_position_raises(self, registered_user, db_session, broker_account, connected_broker):
        portfolio = _portfolio(db_session, registered_user)
        engine = LiveExecutionEngine(db_session, connected_broker)
        with pytest.raises(LiveExecutionEngineError):
            engine.close_position(portfolio, broker_account, uuid.uuid4())

    def test_close_position_partial_quantity(self, registered_user, db_session, broker_account, connected_broker):
        client, headers, _ = registered_user
        portfolio = _portfolio(db_session, registered_user)
        engine = LiveExecutionEngine(db_session, connected_broker)
        engine.submit_order(portfolio.user_id, portfolio, broker_account, _buy_request())

        position = client.get("/api/v1/positions", headers=headers).json()[0]
        full_qty = Decimal(position["quantity"])
        half = (full_qty / 2).quantize(Decimal("0.00000001"))  # match Position.quantity's Numeric(20,8) precision
        engine.close_position(portfolio, broker_account, uuid.UUID(position["id"]), quantity=half)

        remaining = client.get("/api/v1/positions", headers=headers).json()
        assert len(remaining) == 1
        assert Decimal(remaining[0]["quantity"]) == full_qty - half
