"""
Tests for Module 6 -- Order Synchronization Engine. Covers every
detection class, the central duplicate-execution safety guarantee
(unacknowledged orders are NEVER auto-retried), dry-run, idempotency,
and the interplay with position sync after a fill-during-downtime.
"""
import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.application.services.order_sync_service import OrderSyncEngine
from app.application.services.position_sync_service import PositionSyncEngine
from app.domain.broker.broker_models import BrokerOrderRequest, BrokerOrderSide, BrokerOrderType
from app.infrastructure.brokers.mock_broker import MockBrokerAdapter
from app.infrastructure.models.enums import OrderStatus
from app.infrastructure.models.order import Order
from app.infrastructure.models.portfolio import Portfolio


@pytest.fixture()
def portfolio(registered_user, db_session):
    _, _, body = registered_user
    return db_session.query(Portfolio).filter(Portfolio.user_id == uuid.UUID(body["user"]["id"])).first()


@pytest.fixture()
def broker():
    b = MockBrokerAdapter(tick_price=Decimal("100"))
    b.connect()
    return b


def _local_order(db, portfolio, *, status=OrderStatus.PENDING, broker_order_id=None, symbol="AAPL", submitted_at=None):
    order = Order(
        portfolio_id=portfolio.id, symbol=symbol, order_type="limit", side="buy",
        quantity=Decimal("5"), requested_price=Decimal("95"), status=status.value,
        broker_order_id=broker_order_id,
        submitted_at=submitted_at or dt.datetime.now(dt.timezone.utc),
    )
    db.add(order)
    db.commit()
    return order


class TestInSync:
    def test_matching_open_orders_produce_no_discrepancies(self, db_session, portfolio, broker):
        ticket = broker.inject_open_order(symbol="AAPL", side=BrokerOrderSide.BUY, quantity=Decimal("5"), price=Decimal("95"))
        _local_order(db_session, portfolio, broker_order_id=ticket)

        report = OrderSyncEngine(db_session, broker).sync(portfolio)
        assert report.in_sync
        assert report.broker_open_orders_seen == 1
        assert report.local_open_orders_seen == 1


class TestStaleLocalPending:
    def test_marked_cancelled_when_broker_no_longer_lists_it(self, db_session, portfolio, broker):
        order = _local_order(db_session, portfolio, broker_order_id="9999")  # broker book is empty

        report = OrderSyncEngine(db_session, broker).sync(portfolio)
        assert [d.kind for d in report.discrepancies] == ["stale_local_pending"]
        db_session.refresh(order)
        assert order.status == OrderStatus.CANCELLED.value
        assert "position sync" in order.rejection_reason.lower()

    def test_fill_during_downtime_recovered_by_order_then_position_sync(self, db_session, portfolio, broker, registered_user):
        """The designed interplay: a pending order filled while we were
        down. Order sync conservatively cancels the stale local order
        (it cannot fabricate the fill); position sync then finds the
        resulting broker position and recreates it locally. Net effect:
        correct final state, zero fabricated executions."""
        client, headers, _ = registered_user
        order = _local_order(db_session, portfolio, broker_order_id="7777")
        # The fill happened at the broker while we were down:
        broker.place_order(BrokerOrderRequest(symbol="AAPL", side=BrokerOrderSide.BUY, order_type=BrokerOrderType.MARKET, quantity=Decimal("5")))

        OrderSyncEngine(db_session, broker).sync(portfolio)
        db_session.refresh(order)
        assert order.status == OrderStatus.CANCELLED.value

        PositionSyncEngine(db_session, broker).sync(portfolio)
        positions = client.get("/api/v1/positions", headers=headers).json()
        assert len(positions) == 1
        assert Decimal(positions[0]["quantity"]) == Decimal("5")


class TestUnacknowledgedOrder:
    def test_marked_rejected_and_never_retried(self, db_session, portfolio, broker):
        """THE duplicate-execution guarantee. An order with no broker ack
        may or may not have reached the broker -- the only safe move is
        to reject-and-flag, never resend."""
        order = _local_order(db_session, portfolio, broker_order_id=None)
        placed_before = broker._next_ticket

        report = OrderSyncEngine(db_session, broker).sync(portfolio)
        assert [d.kind for d in report.discrepancies] == ["unacknowledged_local_order"]
        db_session.refresh(order)
        assert order.status == OrderStatus.REJECTED.value
        assert "duplicate execution" in order.rejection_reason.lower()
        assert "review manually" in order.rejection_reason.lower()
        assert broker._next_ticket == placed_before  # NOTHING was sent to the broker


class TestOrphanBrokerOrder:
    def test_local_pending_row_created(self, db_session, portfolio, broker, registered_user):
        client, headers, _ = registered_user
        ticket = broker.inject_open_order(symbol="MSFT", side=BrokerOrderSide.SELL, quantity=Decimal("3"), price=Decimal("110"))

        report = OrderSyncEngine(db_session, broker).sync(portfolio)
        assert [d.kind for d in report.discrepancies] == ["orphan_broker_order"]

        orders = client.get("/api/v1/orders", headers=headers).json()
        assert len(orders) == 1
        assert orders[0]["broker_order_id"] == ticket
        assert orders[0]["status"] == "pending"
        assert orders[0]["symbol"] == "MSFT"


class TestDuplicateLocalOrders:
    def test_newer_duplicate_rejected_oldest_kept(self, db_session, portfolio, broker):
        ticket = broker.inject_open_order(symbol="AAPL", side=BrokerOrderSide.BUY, quantity=Decimal("5"), price=Decimal("95"))
        older = _local_order(db_session, portfolio, broker_order_id=ticket, submitted_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
        newer = _local_order(db_session, portfolio, broker_order_id=ticket, submitted_at=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc))

        report = OrderSyncEngine(db_session, broker).sync(portfolio)
        assert any(d.kind == "duplicate_local_orders" for d in report.discrepancies)

        db_session.refresh(older)
        db_session.refresh(newer)
        assert older.status == OrderStatus.PENDING.value  # kept
        assert newer.status == OrderStatus.REJECTED.value
        assert "duplicate" in newer.rejection_reason.lower()


class TestFillStateDrift:
    def test_local_status_updated_to_brokers(self, db_session, portfolio, broker):
        ticket = broker.inject_open_order(
            symbol="AAPL", side=BrokerOrderSide.BUY, quantity=Decimal("5"),
            price=Decimal("95"), filled_quantity=Decimal("2"),
        )
        # Broker book says PENDING; make the broker report partially_filled instead
        from app.domain.broker.broker_models import BrokerOpenOrder, BrokerOrderStatus
        existing = broker._orders[ticket]
        broker._orders[ticket] = BrokerOpenOrder(
            client_order_id=existing.client_order_id, broker_order_id=ticket, symbol=existing.symbol,
            side=existing.side, order_type=existing.order_type, status=BrokerOrderStatus.PARTIALLY_FILLED,
            requested_price=existing.requested_price, quantity=existing.quantity, filled_quantity=Decimal("2"),
        )
        order = _local_order(db_session, portfolio, broker_order_id=ticket)  # local still PENDING

        report = OrderSyncEngine(db_session, broker).sync(portfolio)
        assert any(d.kind == "fill_state_drift" for d in report.discrepancies)
        db_session.refresh(order)
        assert order.status == OrderStatus.PARTIALLY_FILLED.value


class TestDryRunAndIdempotency:
    def test_dry_run_detects_and_audits_but_changes_nothing(self, db_session, portfolio, broker, registered_user):
        client, headers, _ = registered_user
        order = _local_order(db_session, portfolio, broker_order_id="9999")  # stale

        report = OrderSyncEngine(db_session, broker).sync(portfolio, dry_run=True)
        assert len(report.discrepancies) == 1
        assert not report.discrepancies[0].resolved

        db_session.refresh(order)
        assert order.status == OrderStatus.PENDING.value  # untouched

        events = client.get("/api/v1/risk/events", headers=headers).json()
        assert any(e["event_type"] == "order_sync_discrepancy" and "DRY-RUN" in e["description"] for e in events)

    def test_second_sync_finds_nothing(self, db_session, portfolio, broker):
        _local_order(db_session, portfolio, broker_order_id="9999")           # stale
        _local_order(db_session, portfolio, broker_order_id=None)             # unacknowledged
        broker.inject_open_order(symbol="MSFT", side=BrokerOrderSide.BUY, quantity=Decimal("1"))  # orphan broker

        engine = OrderSyncEngine(db_session, broker)
        first = engine.sync(portfolio)
        assert len(first.discrepancies) == 3

        second = engine.sync(portfolio)
        assert second.in_sync

    def test_repeated_sync_never_duplicates_local_orders(self, db_session, portfolio, broker, registered_user):
        client, headers, _ = registered_user
        broker.inject_open_order(symbol="MSFT", side=BrokerOrderSide.BUY, quantity=Decimal("1"))

        engine = OrderSyncEngine(db_session, broker)
        engine.sync(portfolio)
        engine.sync(portfolio)
        engine.sync(portfolio)

        orders = client.get("/api/v1/orders", headers=headers).json()
        assert len(orders) == 1
