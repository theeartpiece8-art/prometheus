"""
Tests for Sprint 4 module 9: the CircuitBreaker domain logic (pure) and
the MonitoredBrokerAdapter wrapper that feeds it, exercised via
MockBrokerAdapter's failure-injection knobs.
"""
from decimal import Decimal

import pytest

from app.domain.broker.broker_models import (
    BrokerConnectionError,
    BrokerOrderRejectedError,
    BrokerOrderRequest,
    BrokerOrderSide,
    BrokerOrderStatus,
    BrokerOrderType,
)
from app.domain.broker.circuit_breaker import BreakerState, CircuitBreaker, CircuitBreakerConfig
from app.infrastructure.brokers.mock_broker import MockBrokerAdapter
from app.infrastructure.brokers.monitored_broker import CircuitOpenError, MonitoredBrokerAdapter


def _buy(symbol="AAPL", quantity=Decimal("1")):
    return BrokerOrderRequest(symbol=symbol, side=BrokerOrderSide.BUY, order_type=BrokerOrderType.MARKET, quantity=quantity)


# ----------------------------------------------------------------------
# Pure domain logic
# ----------------------------------------------------------------------

class TestCircuitBreakerDomain:
    def test_starts_closed(self):
        breaker = CircuitBreaker()
        assert breaker.state == BreakerState.CLOSED
        assert breaker.is_open is False

    def test_trips_after_max_consecutive_failures(self):
        breaker = CircuitBreaker(config=CircuitBreakerConfig(max_consecutive_failures=3))
        breaker.record_failure("timeout 1")
        breaker.record_failure("timeout 2")
        assert breaker.is_open is False
        breaker.record_failure("timeout 3")
        assert breaker.is_open is True
        assert "3 consecutive" in breaker.trip_reason
        assert breaker.tripped_at is not None

    def test_success_resets_the_failure_streak(self):
        breaker = CircuitBreaker(config=CircuitBreakerConfig(max_consecutive_failures=3))
        breaker.record_failure("timeout 1")
        breaker.record_failure("timeout 2")
        breaker.record_success(latency_ms=10)
        breaker.record_failure("timeout 3")
        breaker.record_failure("timeout 4")
        assert breaker.is_open is False  # streak was broken; only 2 consecutive since

    def test_disconnect_trips_immediately_without_counting(self):
        breaker = CircuitBreaker(config=CircuitBreakerConfig(max_consecutive_failures=99))
        breaker.record_disconnect("Broker connection lost.")
        assert breaker.is_open is True
        assert "connection lost" in breaker.trip_reason.lower()

    def test_latency_pattern_trips_single_slow_call_does_not(self):
        breaker = CircuitBreaker(config=CircuitBreakerConfig(max_latency_ms=100, max_latency_violations=3))
        breaker.record_success(latency_ms=500)
        breaker.record_success(latency_ms=500)
        assert breaker.is_open is False
        breaker.record_success(latency_ms=500)
        assert breaker.is_open is True
        assert "latency" in breaker.trip_reason.lower()

    def test_fast_call_resets_latency_violation_streak(self):
        breaker = CircuitBreaker(config=CircuitBreakerConfig(max_latency_ms=100, max_latency_violations=3))
        breaker.record_success(latency_ms=500)
        breaker.record_success(latency_ms=500)
        breaker.record_success(latency_ms=10)  # healthy again
        breaker.record_success(latency_ms=500)
        breaker.record_success(latency_ms=500)
        assert breaker.is_open is False

    def test_second_trip_keeps_original_reason_and_timestamp(self):
        breaker = CircuitBreaker()
        breaker.record_disconnect("first reason")
        first_at = breaker.tripped_at
        breaker.record_disconnect("second reason")
        assert breaker.trip_reason == "first reason"
        assert breaker.tripped_at == first_at

    def test_manual_reset_restores_closed_state(self):
        breaker = CircuitBreaker()
        breaker.record_disconnect("gone")
        breaker.reset()
        assert breaker.state == BreakerState.CLOSED
        assert breaker.trip_reason is None
        assert breaker.consecutive_failures == 0


# ----------------------------------------------------------------------
# The wrapper, against the mock's failure knobs
# ----------------------------------------------------------------------

class TestMonitoredBrokerAdapter:
    def _monitored(self, **breaker_kwargs) -> tuple[MonitoredBrokerAdapter, MockBrokerAdapter, list]:
        inner = MockBrokerAdapter(tick_price=Decimal("100"))
        inner.connect()
        trips: list[str] = []
        monitored = MonitoredBrokerAdapter(
            inner,
            breaker=CircuitBreaker(config=CircuitBreakerConfig(**breaker_kwargs)) if breaker_kwargs else None,
            on_trip=trips.append,
        )
        return monitored, inner, trips

    def test_passes_through_normal_operations(self):
        monitored, _, trips = self._monitored()
        result = monitored.place_order(_buy())
        assert result.status == BrokerOrderStatus.FILLED
        assert monitored.get_positions()[0].symbol == "AAPL"
        assert trips == []
        assert monitored.breaker.is_open is False

    def test_repeated_connection_failures_trip_the_breaker_and_fire_on_trip_once(self):
        monitored, inner, trips = self._monitored(max_consecutive_failures=3)
        inner.tick_should_fail = True

        for _ in range(3):
            with pytest.raises(BrokerConnectionError):
                monitored.get_tick("AAPL")

        assert monitored.breaker.is_open is True
        assert len(trips) == 1  # exactly once, not once per subsequent failure

        # Further failures don't re-fire the callback
        with pytest.raises(BrokerConnectionError):
            monitored.get_tick("AAPL")
        assert len(trips) == 1

    def test_business_rejections_do_not_count_as_failures(self):
        """A run of legitimate rejections is the broker WORKING -- it must
        never shut down healthy connectivity."""
        monitored, inner, trips = self._monitored(max_consecutive_failures=2)
        inner.reject_next_n_orders = 5

        for _ in range(5):
            with pytest.raises(BrokerOrderRejectedError):
                monitored.place_order(_buy())

        assert monitored.breaker.is_open is False
        assert trips == []

    def test_open_breaker_blocks_new_orders(self):
        monitored, inner, trips = self._monitored(max_consecutive_failures=1)
        inner.tick_should_fail = True
        with pytest.raises(BrokerConnectionError):
            monitored.get_tick("AAPL")
        assert monitored.breaker.is_open is True

        inner.tick_should_fail = False  # transport is actually fine again
        with pytest.raises(CircuitOpenError):
            monitored.place_order(_buy())  # still blocked: manual reset required

    def test_open_breaker_still_allows_closing_positions_and_close_all(self):
        """The load-bearing safety property: an open breaker blocks NEW
        risk only. Exits must work precisely when things are on fire."""
        monitored, inner, _ = self._monitored(max_consecutive_failures=1)
        monitored.place_order(_buy(quantity=Decimal("2")))
        ticket = monitored.get_positions()[0].broker_position_id

        inner.tick_should_fail = True
        with pytest.raises(BrokerConnectionError):
            monitored.get_tick("AAPL")
        assert monitored.breaker.is_open is True
        inner.tick_should_fail = False

        result = monitored.close_position(ticket, Decimal("1"))
        assert result.status == BrokerOrderStatus.FILLED
        results = monitored.close_all()
        assert all(r.status == BrokerOrderStatus.FILLED for r in results)
        assert monitored.get_positions() == []

    def test_health_check_reporting_disconnected_trips_immediately(self):
        monitored, inner, trips = self._monitored(max_consecutive_failures=99)
        inner.simulate_disconnect()

        status = monitored.health_check()  # returns (not raises) connected=False
        assert status.connected is False
        assert monitored.breaker.is_open is True
        assert len(trips) == 1
        assert "not connected" in trips[0].lower()

    def test_latency_pattern_trips_via_wrapper(self):
        monitored, inner, trips = self._monitored(max_latency_ms=1, max_latency_violations=2)
        inner._latency_ms = 20  # every mock order sleeps 20ms > 1ms threshold

        monitored.place_order(_buy())
        assert monitored.breaker.is_open is False
        monitored.place_order(_buy())
        assert monitored.breaker.is_open is True
        assert len(trips) == 1
        assert "latency" in trips[0].lower()

    def test_reset_re_arms_breaker_and_trip_notification(self):
        monitored, inner, trips = self._monitored(max_consecutive_failures=1)
        inner.tick_should_fail = True
        with pytest.raises(BrokerConnectionError):
            monitored.get_tick("AAPL")
        assert monitored.breaker.is_open is True and len(trips) == 1

        inner.tick_should_fail = False
        monitored.reset()
        assert monitored.breaker.is_open is False
        result = monitored.place_order(_buy())  # trading works again
        assert result.status == BrokerOrderStatus.FILLED

        # A fresh trip after reset DOES notify again (one-shot per trip, not per lifetime)
        inner.tick_should_fail = True
        with pytest.raises(BrokerConnectionError):
            monitored.get_tick("AAPL")
        assert len(trips) == 2

    def test_deliberate_disconnect_is_not_a_failure(self):
        monitored, _, trips = self._monitored(max_consecutive_failures=1)
        monitored.disconnect()
        assert monitored.breaker.is_open is False
        assert trips == []

    def test_broker_name_passes_through(self):
        monitored, _, _ = self._monitored()
        assert monitored.broker_name == "mock"
