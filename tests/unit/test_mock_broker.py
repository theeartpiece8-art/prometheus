"""
Unit tests for MockBrokerAdapter (Sprint 4 broker foundation). These lock
in the behavior verified ad-hoc during development: connection lifecycle,
order placement and position bookkeeping (open/add/close/flip), rejection
injection, and disconnect/reconnect -- so future changes to the mock
can't silently drift from what the Live Execution Engine tests assume.
"""
from decimal import Decimal

import pytest

from app.domain.broker.broker_models import (
    BrokerConnectionError,
    BrokerConnectionStatus,
    BrokerOrderRejectedError,
    BrokerOrderRequest,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerOrderType,
    BrokerPositionDirection,
)
from app.infrastructure.brokers.mock_broker import MockBrokerAdapter


def _buy(symbol="AAPL", quantity=Decimal("10"), stop_loss=None, take_profit=None, price=None):
    return BrokerOrderRequest(
        symbol=symbol, side=BrokerOrderSide.BUY, order_type=BrokerOrderType.MARKET,
        quantity=quantity, price=price, stop_loss=stop_loss, take_profit=take_profit,
    )


def _sell(symbol="AAPL", quantity=Decimal("10"), price=None):
    return BrokerOrderRequest(symbol=symbol, side=BrokerOrderSide.SELL, order_type=BrokerOrderType.MARKET, quantity=quantity, price=price)


class TestConnectionLifecycle:
    def test_operations_raise_when_not_connected(self):
        broker = MockBrokerAdapter()
        with pytest.raises(BrokerConnectionError):
            broker.get_account()
        with pytest.raises(BrokerConnectionError):
            broker.place_order(_buy())

    def test_connect_makes_operations_available(self):
        broker = MockBrokerAdapter(starting_balance=Decimal("5000"))
        broker.connect()
        assert broker.is_connected() is True
        account = broker.get_account()
        assert account.balance == Decimal("5000")

    def test_connect_can_be_configured_to_fail(self):
        broker = MockBrokerAdapter(connect_should_fail=True)
        with pytest.raises(BrokerConnectionError):
            broker.connect()
        assert broker.is_connected() is False

    def test_disconnect_then_reconnect(self):
        broker = MockBrokerAdapter()
        broker.connect()
        broker.simulate_disconnect()
        assert broker.is_connected() is False
        with pytest.raises(BrokerConnectionError):
            broker.get_account()
        broker.reconnect()
        assert broker.is_connected() is True

    def test_health_check_reports_connection_state(self):
        broker = MockBrokerAdapter()
        broker.connect()
        status = broker.health_check()
        assert status.connected is True
        assert status.latency_ms is not None

    def test_health_check_can_be_configured_to_raise(self):
        broker = MockBrokerAdapter()
        broker.connect()
        broker.raise_on_health_check = True
        with pytest.raises(BrokerConnectionError):
            broker.health_check()

    def test_broker_name_is_stable(self):
        assert MockBrokerAdapter().broker_name == "mock"


class TestOrderPlacementAndPositions:
    def test_market_buy_opens_a_long_position(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        result = broker.place_order(_buy(quantity=Decimal("10"), stop_loss=Decimal("90"), take_profit=Decimal("120")))

        assert result.status == BrokerOrderStatus.FILLED
        assert result.executed_price == Decimal("100")
        assert result.broker_order_id is not None

        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].direction == BrokerPositionDirection.LONG
        assert positions[0].quantity == Decimal("10")
        assert positions[0].stop_loss == Decimal("90")
        assert positions[0].take_profit == Decimal("120")

    def test_market_sell_with_no_existing_position_opens_a_short(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        broker.place_order(_sell(quantity=Decimal("5")))
        positions = broker.get_positions()
        assert positions[0].direction == BrokerPositionDirection.SHORT

    def test_same_direction_fill_adds_with_weighted_average_price(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        broker.place_order(_buy(quantity=Decimal("10")))
        broker.set_tick_price(Decimal("110"))
        broker.place_order(_buy(quantity=Decimal("10")))

        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].quantity == Decimal("20")
        assert positions[0].average_price == Decimal("105")  # (100*10 + 110*10) / 20

    def test_opposite_direction_fill_reduces_position(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        broker.place_order(_buy(quantity=Decimal("10")))
        broker.place_order(_sell(quantity=Decimal("4")))

        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].quantity == Decimal("6")

    def test_opposite_direction_fill_fully_closes_position(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        broker.place_order(_buy(quantity=Decimal("10")))
        broker.place_order(_sell(quantity=Decimal("10")))
        assert broker.get_positions() == []

    def test_opposite_direction_fill_larger_than_position_flips_it(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        broker.place_order(_buy(quantity=Decimal("10")))
        broker.place_order(_sell(quantity=Decimal("15")))

        positions = broker.get_positions()
        assert len(positions) == 1
        assert positions[0].direction == BrokerPositionDirection.SHORT
        assert positions[0].quantity == Decimal("5")

    def test_get_positions_filters_by_symbol(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        broker.place_order(_buy(symbol="AAPL", quantity=Decimal("1")))
        broker.place_order(_buy(symbol="MSFT", quantity=Decimal("2")))
        assert len(broker.get_positions(symbol="AAPL")) == 1
        assert len(broker.get_positions()) == 2


class TestClosePositionAndCloseAll:
    def test_close_position_full(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        broker.place_order(_buy(quantity=Decimal("10")))
        broker.set_tick_price(Decimal("120"))
        ticket = broker.get_positions()[0].broker_position_id

        result = broker.close_position(ticket)
        assert result.status == BrokerOrderStatus.FILLED
        assert result.executed_price == Decimal("120")
        assert broker.get_positions() == []

    def test_close_position_partial(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        broker.place_order(_buy(quantity=Decimal("10")))
        ticket = broker.get_positions()[0].broker_position_id

        broker.close_position(ticket, Decimal("4"))
        remaining = broker.get_positions()
        assert len(remaining) == 1
        assert remaining[0].quantity == Decimal("6")

    def test_close_position_unknown_ticket_raises(self):
        broker = MockBrokerAdapter()
        broker.connect()
        with pytest.raises(BrokerOrderRejectedError):
            broker.close_position("nonexistent-ticket")

    def test_close_all_closes_every_open_position(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        broker.place_order(_buy(symbol="AAPL", quantity=Decimal("1")))
        broker.place_order(_buy(symbol="MSFT", quantity=Decimal("2")))
        assert len(broker.get_positions()) == 2

        results = broker.close_all()
        assert len(results) == 2
        assert all(r.status == BrokerOrderStatus.FILLED for r in results)
        assert broker.get_positions() == []

    def test_close_all_on_empty_book_returns_empty_list(self):
        broker = MockBrokerAdapter()
        broker.connect()
        assert broker.close_all() == []


class TestFailureInjection:
    def test_reject_next_n_orders(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        broker.reject_next_n_orders = 2
        broker.reject_reason = "Simulated margin failure"

        with pytest.raises(BrokerOrderRejectedError, match="Simulated margin failure"):
            broker.place_order(_buy())
        with pytest.raises(BrokerOrderRejectedError):
            broker.place_order(_buy())

        # Third attempt succeeds -- counter is exhausted
        result = broker.place_order(_buy())
        assert result.status == BrokerOrderStatus.FILLED

    def test_tick_should_fail_flag(self):
        broker = MockBrokerAdapter()
        broker.connect()
        broker.tick_should_fail = True
        with pytest.raises(BrokerConnectionError):
            broker.get_tick("AAPL")


class TestAccountAndSymbols:
    def test_get_symbols_returns_tradable_instruments(self):
        broker = MockBrokerAdapter()
        broker.connect()
        symbols = broker.get_symbols()
        assert len(symbols) > 0
        assert all(s.tradable for s in symbols)

    def test_get_tick_returns_bid_ask_around_configured_price(self):
        broker = MockBrokerAdapter(tick_price=Decimal("100"))
        broker.connect()
        tick = broker.get_tick("AAPL")
        assert tick.bid < Decimal("100") < tick.ask

    def test_account_equity_reflects_unrealized_pnl(self):
        broker = MockBrokerAdapter(starting_balance=Decimal("10000"), tick_price=Decimal("100"))
        broker.connect()
        broker.place_order(_buy(quantity=Decimal("10")))
        broker.set_tick_price(Decimal("110"))

        account = broker.get_account()
        assert account.balance == Decimal("10000")  # unrealized, balance untouched
        assert account.equity == Decimal("10100")  # 10000 + (10 * (110-100))
