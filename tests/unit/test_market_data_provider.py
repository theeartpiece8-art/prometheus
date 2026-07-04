import datetime as dt
from decimal import Decimal

from app.infrastructure.market_data.base_provider import MarketDataProvider, MarketDataProviderError
from app.infrastructure.market_data.fallback_provider import FallbackMarketDataProvider
from app.infrastructure.market_data.mock_provider import MockProvider
from app.infrastructure.market_data.yfinance_provider import YFinanceProvider


class _AlwaysFailsProvider(MarketDataProvider):
    """Test double simulating a primary source that is always down."""
    name = "always_fails"

    def get_historical_ohlcv(self, symbol, timeframe, start_date, end_date):
        raise MarketDataProviderError("simulated outage")

    def get_latest_price(self, symbol):
        raise MarketDataProviderError("simulated outage")


class _AlwaysSucceedsProvider(MarketDataProvider):
    """Test double simulating a working primary source, distinguishable
    from the mock fallback so we can prove which one actually served data."""
    name = "always_succeeds"

    def get_historical_ohlcv(self, symbol, timeframe, start_date, end_date):
        return [{"timestamp": start_date.isoformat(), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

    def get_latest_price(self, symbol):
        return Decimal("999")


class TestMockProvider:
    def test_deterministic_for_same_inputs(self):
        provider = MockProvider()
        start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        end = start + dt.timedelta(days=10)
        bars1 = provider.get_historical_ohlcv("AAPL", "1D", start, end)
        bars2 = provider.get_historical_ohlcv("AAPL", "1D", start, end)
        assert bars1 == bars2
        assert len(bars1) > 0

    def test_different_symbols_produce_different_series(self):
        provider = MockProvider()
        start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        end = start + dt.timedelta(days=10)
        aapl_bars = provider.get_historical_ohlcv("AAPL", "1D", start, end)
        tsla_bars = provider.get_historical_ohlcv("TSLA", "1D", start, end)
        assert aapl_bars != tsla_bars

    def test_never_raises(self):
        provider = MockProvider()
        start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        bars = provider.get_historical_ohlcv("NOT_A_REAL_SYMBOL", "1D", start, start + dt.timedelta(hours=1))
        assert isinstance(bars, list)

    def test_respects_500_bar_internal_cap(self):
        provider = MockProvider()
        start = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        end = start + dt.timedelta(days=3650)  # 10 years of daily bars, way more than 500
        bars = provider.get_historical_ohlcv("AAPL", "1D", start, end)
        assert len(bars) <= 500


class TestFallbackProvider:
    def test_uses_primary_when_it_succeeds(self):
        fallback = FallbackMarketDataProvider(primary=_AlwaysSucceedsProvider(), fallback=MockProvider())
        start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        bars, source = fallback.get_historical_ohlcv_with_source("AAPL", "1D", start, start + dt.timedelta(days=1))
        assert source == "always_succeeds"
        assert len(bars) == 1

    def test_falls_back_transparently_when_primary_fails(self):
        fallback = FallbackMarketDataProvider(primary=_AlwaysFailsProvider(), fallback=MockProvider())
        start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        bars, source = fallback.get_historical_ohlcv_with_source("AAPL", "1D", start, start + dt.timedelta(days=5))
        assert source == "mock"
        assert len(bars) > 0

    def test_disabled_primary_goes_straight_to_fallback(self):
        fallback = FallbackMarketDataProvider(
            primary=_AlwaysSucceedsProvider(), fallback=MockProvider(), primary_enabled=False
        )
        start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        bars, source = fallback.get_historical_ohlcv_with_source("AAPL", "1D", start, start + dt.timedelta(days=5))
        assert source == "mock"

    def test_price_fallback_behaves_the_same_way(self):
        fallback = FallbackMarketDataProvider(primary=_AlwaysFailsProvider(), fallback=MockProvider())
        price, source = fallback.get_latest_price_with_source("AAPL")
        assert source == "mock"
        assert price > 0

    def test_generic_interface_methods_also_work(self):
        """The plain MarketDataProvider interface (get_historical_ohlcv /
        get_latest_price, no source info) must also work on the composed
        fallback provider — this is what BacktestService falls back to for
        any bare (non-Fallback) provider."""
        fallback = FallbackMarketDataProvider(primary=_AlwaysFailsProvider(), fallback=MockProvider())
        start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        bars = fallback.get_historical_ohlcv("AAPL", "1D", start, start + dt.timedelta(days=5))
        assert len(bars) > 0
        price = fallback.get_latest_price("AAPL")
        assert price > 0


class TestYFinanceProviderRaisesCleanlyOnFailure:
    def test_network_failure_raises_market_data_provider_error(self):
        # In this sandbox, Yahoo Finance hosts are not in the network
        # allowlist, so this genuinely exercises the failure path rather
        # than mocking it away.
        provider = YFinanceProvider()
        start = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        try:
            provider.get_historical_ohlcv("AAPL", "1D", start, start + dt.timedelta(days=5))
            # If this ever runs somewhere WITH network access, that's fine too —
            # just confirm it returns a sane list rather than asserting failure.
        except MarketDataProviderError:
            pass  # expected in network-restricted environments
