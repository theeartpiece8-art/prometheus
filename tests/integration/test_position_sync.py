"""
Tests for Module 5 -- Position Synchronization Engine. Covers every
drift class, dry-run (zero writes), the idempotency guarantee (second
sync finds nothing), audit records, and P&L application on
reconciliation closes.
"""
import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.application.services.position_sync_service import PositionSyncEngine
from app.domain.broker.broker_models import BrokerOrderRequest, BrokerOrderSide, BrokerOrderType
from app.infrastructure.brokers.mock_broker import MockBrokerAdapter
from app.infrastructure.models.enums import PositionStatus
from app.infrastructure.models.portfolio import Portfolio
from app.infrastructure.models.position import Position


@pytest.fixture()
def portfolio(registered_user, db_session):
    _, _, body = registered_user
    return db_session.query(Portfolio).filter(Portfolio.user_id == uuid.UUID(body["user"]["id"])).first()


@pytest.fixture()
def broker():
    b = MockBrokerAdapter(tick_price=Decimal("100"))
    b.connect()
    return b


def _broker_buy(broker, symbol="AAPL", qty=Decimal("10"), sl=None, tp=None):
    broker.place_order(
        BrokerOrderRequest(symbol=symbol, side=BrokerOrderSide.BUY, order_type=BrokerOrderType.MARKET, quantity=qty, stop_loss=sl, take_profit=tp)
    )


def _local_position(db, portfolio, symbol="AAPL", direction="long", qty=Decimal("10"), avg=Decimal("100")):
    pos = Position(
        portfolio_id=portfolio.id, symbol=symbol, direction=direction, quantity=qty,
        average_price=avg, current_price=avg, opened_at=dt.datetime.now(dt.timezone.utc),
        status=PositionStatus.OPEN.value,
    )
    db.add(pos)
    db.commit()
    return pos


class TestInSync:
    def test_matching_states_produce_no_discrepancies(self, db_session, portfolio, broker):
        _broker_buy(broker, qty=Decimal("10"))
        _local_position(db_session, portfolio, qty=Decimal("10"), avg=Decimal("100"))

        report = PositionSyncEngine(db_session, broker).sync(portfolio)
        assert report.in_sync
        assert report.broker_positions_seen == 1
        assert report.local_positions_seen == 1


class TestOrphanBrokerPosition:
    def test_detected_and_local_created(self, db_session, portfolio, broker, registered_user):
        client, headers, _ = registered_user
        _broker_buy(broker, symbol="AAPL", qty=Decimal("5"), sl=Decimal("90"), tp=Decimal("120"))

        report = PositionSyncEngine(db_session, broker).sync(portfolio)
        assert len(report.discrepancies) == 1
        d = report.discrepancies[0]
        assert d.kind == "orphan_broker_position" and d.resolved

        positions = client.get("/api/v1/positions", headers=headers).json()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "AAPL"
        assert Decimal(positions[0]["quantity"]) == Decimal("5")
        assert Decimal(positions[0]["stop_loss"]) == Decimal("90")  # broker's protective levels carried over

    def test_audited_as_risk_event(self, db_session, portfolio, broker, registered_user):
        client, headers, _ = registered_user
        _broker_buy(broker)
        PositionSyncEngine(db_session, broker).sync(portfolio)

        events = client.get("/api/v1/risk/events", headers=headers).json()
        sync_events = [e for e in events if e["event_type"] == "position_sync_discrepancy"]
        assert len(sync_events) == 1
        assert "orphan_broker_position" in sync_events[0]["description"]
        assert "RESOLVED" in sync_events[0]["description"]


class TestOrphanLocalPosition:
    def test_detected_and_closed_with_pnl_at_broker_tick(self, db_session, portfolio, broker):
        # Local long 10 @ 100; broker has nothing; current tick 120 -> +200 realized
        lpos = _local_position(db_session, portfolio, qty=Decimal("10"), avg=Decimal("100"))
        broker.set_tick_price(Decimal("120"))
        before = portfolio.balance

        report = PositionSyncEngine(db_session, broker).sync(portfolio)
        d = report.discrepancies[0]
        assert d.kind == "orphan_local_position" and d.resolved

        db_session.refresh(lpos)
        assert lpos.status == PositionStatus.CLOSED.value
        assert lpos.closed_at is not None
        # bid = 120 - spread(120*0.0001=0.012) -> realized = (119.988-100)*10 = 199.88
        assert portfolio.balance - before == Decimal("199.8800")

    def test_short_orphan_local_realizes_correctly(self, db_session, portfolio, broker):
        _local_position(db_session, portfolio, direction="short", qty=Decimal("10"), avg=Decimal("100"))
        broker.set_tick_price(Decimal("80"))
        before = portfolio.balance

        PositionSyncEngine(db_session, broker).sync(portfolio)
        # short closes at ask = 80 + 0.008 -> realized = (100 - 80.008)*10 = 199.92
        assert portfolio.balance - before == Decimal("199.9200")


class TestFieldDrift:
    def test_quantity_mismatch_updates_local_to_broker(self, db_session, portfolio, broker):
        _broker_buy(broker, qty=Decimal("7"))
        lpos = _local_position(db_session, portfolio, qty=Decimal("10"))

        report = PositionSyncEngine(db_session, broker).sync(portfolio)
        kinds = [d.kind for d in report.discrepancies]
        assert "quantity_mismatch" in kinds
        db_session.refresh(lpos)
        assert lpos.quantity == Decimal("7")

    def test_average_price_mismatch_updates_local_to_broker(self, db_session, portfolio, broker):
        _broker_buy(broker, qty=Decimal("10"))  # fills at 100
        lpos = _local_position(db_session, portfolio, qty=Decimal("10"), avg=Decimal("95"))

        report = PositionSyncEngine(db_session, broker).sync(portfolio)
        assert any(d.kind == "average_price_mismatch" for d in report.discrepancies)
        db_session.refresh(lpos)
        assert lpos.average_price == Decimal("100")

    def test_direction_mismatch_closes_and_recreates_from_broker(self, db_session, portfolio, broker, registered_user):
        client, headers, _ = registered_user
        broker.place_order(
            BrokerOrderRequest(symbol="AAPL", side=BrokerOrderSide.SELL, order_type=BrokerOrderType.MARKET, quantity=Decimal("4"))
        )  # broker is SHORT 4
        _local_position(db_session, portfolio, direction="long", qty=Decimal("10"))

        report = PositionSyncEngine(db_session, broker).sync(portfolio)
        assert any(d.kind == "direction_mismatch" and d.resolved for d in report.discrepancies)

        open_positions = client.get("/api/v1/positions", headers=headers).json()
        assert len(open_positions) == 1
        assert open_positions[0]["direction"] == "short"
        assert Decimal(open_positions[0]["quantity"]) == Decimal("4")

    def test_direction_mismatch_does_not_also_report_quantity_noise(self, db_session, portfolio, broker):
        """A direction flip makes quantity/price comparisons meaningless;
        the engine must report the flip once, not three overlapping
        discrepancies for the same root cause."""
        broker.place_order(
            BrokerOrderRequest(symbol="AAPL", side=BrokerOrderSide.SELL, order_type=BrokerOrderType.MARKET, quantity=Decimal("4"))
        )
        _local_position(db_session, portfolio, direction="long", qty=Decimal("10"))

        report = PositionSyncEngine(db_session, broker).sync(portfolio)
        assert [d.kind for d in report.discrepancies] == ["direction_mismatch"]


class TestDryRun:
    def test_dry_run_detects_everything_and_writes_nothing(self, db_session, portfolio, broker, registered_user):
        client, headers, _ = registered_user
        _broker_buy(broker, symbol="MSFT", qty=Decimal("3"))     # orphan broker
        lpos = _local_position(db_session, portfolio, symbol="AAPL", qty=Decimal("10"))  # orphan local
        before_balance = portfolio.balance

        report = PositionSyncEngine(db_session, broker).sync(portfolio, dry_run=True)

        assert len(report.discrepancies) == 2
        assert all(not d.resolved for d in report.discrepancies)
        assert report.dry_run is True

        # Zero state changes: local position still open, no MSFT created, balance untouched
        db_session.refresh(lpos)
        assert lpos.status == PositionStatus.OPEN.value
        positions = client.get("/api/v1/positions", headers=headers).json()
        assert {p["symbol"] for p in positions} == {"AAPL"}
        assert portfolio.balance == before_balance

        # But the observations ARE audited (marked as dry-run)
        events = client.get("/api/v1/risk/events", headers=headers).json()
        dry_events = [e for e in events if "DRY-RUN" in e["description"]]
        assert len(dry_events) == 2


class TestIdempotency:
    def test_second_sync_after_reconciliation_finds_nothing(self, db_session, portfolio, broker):
        """The Module 5 idempotency guarantee: reconcile once, and an
        immediate re-sync detects zero discrepancies and changes nothing."""
        _broker_buy(broker, symbol="AAPL", qty=Decimal("5"))
        _broker_buy(broker, symbol="MSFT", qty=Decimal("3"))
        _local_position(db_session, portfolio, symbol="TSLA", qty=Decimal("2"))  # orphan local

        engine = PositionSyncEngine(db_session, broker)
        first = engine.sync(portfolio)
        assert len(first.discrepancies) == 3

        second = engine.sync(portfolio)
        assert second.in_sync
        assert second.broker_positions_seen == 2
        assert second.local_positions_seen == 2  # AAPL + MSFT now local; TSLA closed

    def test_repeated_sync_never_duplicates_positions(self, db_session, portfolio, broker, registered_user):
        client, headers, _ = registered_user
        _broker_buy(broker, qty=Decimal("5"))

        engine = PositionSyncEngine(db_session, broker)
        engine.sync(portfolio)
        engine.sync(portfolio)
        engine.sync(portfolio)

        positions = client.get("/api/v1/positions", headers=headers).json()
        assert len(positions) == 1  # not three
