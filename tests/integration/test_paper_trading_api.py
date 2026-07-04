"""
Integration tests for the Paper Trading Engine (Sprint 3), covering
09_Paper_Trading_Engine.md's "Testing Requirements": simulated order
execution, risk engine integration, portfolio calculations, PnL
calculations, position management, notification delivery, and session
recovery after interruption.

Deterministic trading behavior is driven by injected provider doubles
(a rigged uptrend, a controllable fixed price) — never by hoping the
default mock random walk happens to produce a crossover.
"""
import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.application.schemas.paper_trading import SessionItemRequest, StartSessionRequest
from app.application.services.paper_trading_service import (
    PaperTradingService,
    mark_interrupted_sessions,
)
from app.infrastructure.market_data.base_provider import MarketDataProvider, MarketDataProviderError


@pytest.fixture()
def live_price(monkeypatch):
    """OrderService prices fills (and position closes) via the module-level
    default provider, NOT via the provider injected into
    PaperTradingService — that's real production behavior, but for
    deterministic tests the fill price must agree with the injected
    provider's world. This patches order_service's imported
    get_latest_price to a controllable holder; tests mutate
    holder['price'] to move 'the market'."""
    holder = {"price": Decimal("110")}

    def _fake(symbol):
        return holder["price"], "test_double"

    monkeypatch.setattr("app.application.services.order_service.get_latest_price", _fake)
    return holder


# ----------------------------------------------------------------------
# Provider test doubles
# ----------------------------------------------------------------------

class _TrendProvider(MarketDataProvider):
    """59 flat bars then ONE final jump bar — engineered so the 5/20 SMA
    crossover occurs exactly at the LAST bar. This matters: unlike the
    backtest engine (which walks bar-by-bar and catches a cross whenever
    it happens), a paper tick evaluates the strategy ONCE on the full
    window, and MovingAverageCrossoverStrategy only signals on a fresh
    cross at the final bar (fast_prev <= slow_prev AND fast_now > slow_now).
    With flat 100s, prev SMAs are equal (<= holds); a last close of 110
    gives fast=(4*100+110)/5=102 > slow=(19*100+110)/20=100.5 -> BUY."""
    name = "trend_double"

    def __init__(self, latest_price: Decimal = Decimal("110")) -> None:
        self.latest_price = latest_price

    def get_historical_ohlcv(self, symbol, timeframe, start_date, end_date):
        prices = [100.0] * 59 + [110.0]
        bars = []
        t = start_date
        for p in prices:
            bars.append(
                {"timestamp": t.isoformat(), "open": p, "high": p * 1.002, "low": p * 0.998, "close": p, "volume": 1000}
            )
            t += dt.timedelta(days=1)
        return bars

    def get_latest_price(self, symbol):
        return self.latest_price


class _FlatProvider(MarketDataProvider):
    """Perfectly flat prices — never produces a crossover signal."""
    name = "flat_double"

    def get_historical_ohlcv(self, symbol, timeframe, start_date, end_date):
        bars = []
        t = start_date
        for _ in range(60):
            bars.append(
                {"timestamp": t.isoformat(), "open": 100, "high": 100.2, "low": 99.8, "close": 100, "volume": 1000}
            )
            t += dt.timedelta(days=1)
        return bars

    def get_latest_price(self, symbol):
        return Decimal("100")


class _BrokenProvider(MarketDataProvider):
    """Always fails — exercises the Data Feed Interrupted path."""
    name = "broken_double"

    def get_historical_ohlcv(self, symbol, timeframe, start_date, end_date):
        raise MarketDataProviderError("simulated outage")

    def get_latest_price(self, symbol):
        raise MarketDataProviderError("simulated outage")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _make_enabled_strategy(client, headers, stop_loss_pct=8.0) -> dict:
    strat = client.post(
        "/api/v1/strategies",
        json={
            "name": "PT Strategy",
            "strategy_type": "moving_average_crossover",
            "parameters": {
                "fast_period": 5, "slow_period": 20,
                "stop_loss_pct": stop_loss_pct, "take_profit_pct": stop_loss_pct * 2,
            },
        },
        headers=headers,
    ).json()
    client.post(f"/api/v1/strategies/{strat['id']}/enable", headers=headers)
    return strat


def _start_session(client, headers, strategy_id, symbol="AAPL") -> dict:
    resp = client.post(
        "/api/v1/paper/start",
        json={"items": [{"strategy_id": strategy_id, "symbol": symbol, "timeframe": "1D"}]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _user_id(body) -> uuid.UUID:
    return uuid.UUID(body["user"]["id"])


# ----------------------------------------------------------------------
# Session lifecycle & validation
# ----------------------------------------------------------------------

class TestSessionValidation:
    def test_disabled_strategy_rejects_startup_with_no_session_created(self, registered_user):
        client, headers, _ = registered_user
        strat = client.post(
            "/api/v1/strategies",
            json={"name": "Draft", "strategy_type": "moving_average_crossover", "parameters": {}},
            headers=headers,
        ).json()  # left in 'draft' status

        resp = client.post(
            "/api/v1/paper/start",
            json={"items": [{"strategy_id": strat["id"], "symbol": "AAPL"}]},
            headers=headers,
        )
        assert resp.status_code == 422
        assert len(client.get("/api/v1/paper/sessions", headers=headers).json()) == 0

    def test_unsupported_symbol_rejects_startup(self, registered_user):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        resp = client.post(
            "/api/v1/paper/start",
            json={"items": [{"strategy_id": strat["id"], "symbol": "NOT_A_SYMBOL"}]},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_nonexistent_strategy_rejects_startup(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/paper/start",
            json={"items": [{"strategy_id": str(uuid.uuid4()), "symbol": "AAPL"}]},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_duplicate_items_rejected_at_schema_level(self, registered_user):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        item = {"strategy_id": strat["id"], "symbol": "AAPL", "timeframe": "1D"}
        resp = client.post("/api/v1/paper/start", json={"items": [item, item]}, headers=headers)
        assert resp.status_code == 422

    def test_tick_interval_below_floor_rejected(self, registered_user):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        resp = client.post(
            "/api/v1/paper/start",
            json={"items": [{"strategy_id": strat["id"], "symbol": "AAPL"}], "tick_interval_seconds": 1},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_endpoints_require_authentication(self, client):
        assert client.get("/api/v1/paper/status").status_code in (401, 403)
        assert client.post("/api/v1/paper/start", json={"items": []}).status_code in (401, 403)


class TestSessionLifecycle:
    def test_start_pause_resume_stop_flow(self, registered_user):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        session = _start_session(client, headers, strat["id"])
        sid = session["id"]

        r = client.post("/api/v1/paper/pause", json={"session_id": sid}, headers=headers)
        assert r.json()["status"] == "paused"
        r = client.post("/api/v1/paper/resume", json={"session_id": sid}, headers=headers)
        assert r.json()["status"] == "running"
        r = client.post("/api/v1/paper/stop", json={"session_id": sid}, headers=headers)
        assert r.json()["status"] == "stopped"

    def test_invalid_transitions_return_409(self, registered_user):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = _start_session(client, headers, strat["id"])["id"]

        # resume a running session
        assert client.post("/api/v1/paper/resume", json={"session_id": sid}, headers=headers).status_code == 409
        client.post("/api/v1/paper/stop", json={"session_id": sid}, headers=headers)
        # pause a stopped session
        assert client.post("/api/v1/paper/pause", json={"session_id": sid}, headers=headers).status_code == 409
        # stop a stopped session
        assert client.post("/api/v1/paper/stop", json={"session_id": sid}, headers=headers).status_code == 409

    def test_session_history_remains_after_stop(self, registered_user):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = _start_session(client, headers, strat["id"])["id"]
        client.post("/api/v1/paper/stop", json={"session_id": sid}, headers=headers)

        sessions = client.get("/api/v1/paper/sessions", headers=headers).json()
        assert len(sessions) == 1
        assert sessions[0]["status"] == "stopped"

    def test_users_cannot_touch_each_others_sessions(self, client):
        client.post("/api/v1/auth/register", json={"username": "owner_pt", "email": "opt@x.com", "password": "S3curePass123"})
        r1 = client.post("/api/v1/auth/login", json={"email": "opt@x.com", "password": "S3curePass123"})
        h1 = {"Authorization": f"Bearer {r1.json()['access_token']}"}
        strat = _make_enabled_strategy(client, h1)
        sid = _start_session(client, h1, strat["id"])["id"]

        client.post("/api/v1/auth/register", json={"username": "intr_pt", "email": "ipt@x.com", "password": "S3curePass123"})
        r2 = client.post("/api/v1/auth/login", json={"email": "ipt@x.com", "password": "S3curePass123"})
        h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}

        assert client.get(f"/api/v1/paper/sessions/{sid}", headers=h2).status_code == 404
        assert client.post("/api/v1/paper/stop", json={"session_id": sid}, headers=h2).status_code == 404
        assert client.post(f"/api/v1/paper/sessions/{sid}/tick", headers=h2).status_code == 404


class TestAccountReset:
    def test_reset_refused_with_active_session(self, registered_user):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        _start_session(client, headers, strat["id"])
        assert client.post("/api/v1/paper/reset", json={}, headers=headers).status_code == 409

    def test_reset_refused_with_open_position(self, registered_user):
        client, headers, _ = registered_user
        client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 50},
            headers=headers,
        )
        assert client.post("/api/v1/paper/reset", json={}, headers=headers).status_code == 409

    def test_reset_restores_clean_portfolio(self, registered_user):
        client, headers, _ = registered_user
        # Trade and close so the balance has drifted from 10000
        client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 50},
            headers=headers,
        )
        pos = client.get("/api/v1/positions", headers=headers).json()[0]
        client.post(f"/api/v1/positions/close/{pos['id']}", headers=headers)

        r = client.post("/api/v1/paper/reset", json={"starting_balance": 50000}, headers=headers)
        assert r.status_code == 200
        portfolio = client.get("/api/v1/portfolio", headers=headers).json()
        assert float(portfolio["balance"]) == 50000.0
        assert float(portfolio["equity"]) == 50000.0
        assert float(portfolio["realized_pnl"]) == 0.0


# ----------------------------------------------------------------------
# The tick loop: simulated order execution + risk integration
# ----------------------------------------------------------------------

class TestTickTrading:
    def test_uptrend_signal_opens_position_through_real_order_pipeline(self, registered_user, db_session, live_price):
        """The central Sprint 3 property: a strategy signal during a tick
        creates a REAL Order row (risk-approved, filled) and a REAL
        Position row — the same tables and pipeline manual orders use."""
        client, headers, body = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        service = PaperTradingService(db_session, market_data_provider=_TrendProvider())
        result = service.run_tick(sid)

        assert result.items_evaluated == 1
        assert len(result.actions) == 1
        assert result.actions[0].action == "opened"
        assert result.actions[0].order_id is not None

        positions = client.get("/api/v1/positions", headers=headers).json()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "AAPL"
        assert positions[0]["direction"] == "long"

        orders = client.get("/api/v1/orders", headers=headers).json()
        assert len(orders) == 1
        assert orders[0]["status"] == "filled"
        assert orders[0]["strategy_id"] == strat["id"]

    def test_position_carries_signal_stop_loss_and_take_profit(self, registered_user, db_session, live_price):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers, stop_loss_pct=8.0)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        PaperTradingService(db_session, market_data_provider=_TrendProvider()).run_tick(sid)

        positions = client.get("/api/v1/positions", headers=headers).json()
        assert positions[0]["stop_loss"] is not None
        assert positions[0]["take_profit"] is not None
        entry = Decimal(positions[0]["average_price"])
        assert Decimal(positions[0]["stop_loss"]) < entry < Decimal(positions[0]["take_profit"])

    def test_flat_market_produces_no_trades(self, registered_user, db_session, live_price):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        result = PaperTradingService(db_session, market_data_provider=_FlatProvider()).run_tick(sid)
        assert result.actions == []
        assert len(client.get("/api/v1/positions", headers=headers).json()) == 0

    def test_same_direction_signal_on_repeat_ticks_is_risk_bounded(self, registered_user, db_session, live_price):
        """The trend provider keeps signaling BUY every tick. The risk
        pipeline's symbol-exposure cap must bound repeat entries — what
        must NOT happen is unlimited pyramiding growing exposure without
        limit tick after tick."""
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        service = PaperTradingService(db_session, market_data_provider=_TrendProvider())
        service.run_tick(sid)
        exposure_after_one = Decimal(
            client.get("/api/v1/portfolio/exposure", headers=headers).json()["portfolio_exposure_pct"]
        )
        assert exposure_after_one > 0  # a position genuinely opened
        service.run_tick(sid)
        service.run_tick(sid)
        exposure_after_three = Decimal(
            client.get("/api/v1/portfolio/exposure", headers=headers).json()["portfolio_exposure_pct"]
        )

        # Exposure must remain within the configured symbol cap (20% default)
        assert exposure_after_three <= Decimal("20")
        # And any growth from tick 1 to tick 3 must be risk-bounded, not doubling each tick
        assert exposure_after_three < exposure_after_one * 3

    def test_risk_engine_blocks_tick_trades_exactly_like_manual_orders(self, registered_user, db_session, live_price):
        """Risk integration per the spec: 'Risk limits behave exactly as
        they would in live trading.' Restrict allowed_symbols through the
        real settings API; the automated tick must be rejected."""
        client, headers, _ = registered_user
        client.put("/api/v1/risk/settings", json={"allowed_symbols": ["MSFT"]}, headers=headers)
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        result = PaperTradingService(db_session, market_data_provider=_TrendProvider()).run_tick(sid)

        assert result.actions == []
        assert len(result.rejections) == 1
        assert "allowed symbols" in result.rejections[0].reason.lower()
        assert len(client.get("/api/v1/positions", headers=headers).json()) == 0
        # The rejection is audited as a risk event, same as a manual order
        assert len(client.get("/api/v1/risk/events", headers=headers).json()) >= 1

    def test_disabling_strategy_mid_session_stops_its_trading(self, registered_user, db_session, live_price):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])
        client.post(f"/api/v1/strategies/{strat['id']}/disable", headers=headers)

        result = PaperTradingService(db_session, market_data_provider=_TrendProvider()).run_tick(sid)
        assert result.actions == []
        assert len(result.rejections) == 1
        assert "not active" in result.rejections[0].reason.lower()

    def test_tick_on_non_running_session_is_safe_noop(self, registered_user, db_session, live_price):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])
        client.post("/api/v1/paper/stop", json={"session_id": str(sid)}, headers=headers)

        result = PaperTradingService(db_session, market_data_provider=_TrendProvider()).run_tick(sid)
        assert result.items_evaluated == 0
        assert result.actions == []


# ----------------------------------------------------------------------
# Position management: SL/TP monitoring, PnL, notifications
# ----------------------------------------------------------------------

class TestStopLossTakeProfitMonitoring:
    def _open_long_with_levels(self, client, headers, stop_loss, take_profit):
        r = client.post(
            "/api/v1/orders/place",
            json={
                "symbol": "AAPL", "side": "buy", "order_type": "market",
                "stop_loss": float(stop_loss), "take_profit": float(take_profit),
            },
            headers=headers,
        )
        assert r.json()["order"]["status"] == "filled", r.text
        return client.get("/api/v1/positions", headers=headers).json()[0]

    def test_stop_loss_hit_closes_position_and_notifies(self, registered_user, db_session, live_price):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        live_price["price"] = Decimal("100")
        self._open_long_with_levels(client, headers, stop_loss=90, take_profit=200)

        # The market crashes below the stop: both the monitoring provider's
        # view AND the fill price for the close reflect the crash.
        live_price["price"] = Decimal("50")
        provider = _FlatProvider()
        provider.get_latest_price = lambda symbol: Decimal("50")
        result = PaperTradingService(db_session, market_data_provider=provider).run_tick(sid)

        assert any(a.action == "closed_stop_loss" for a in result.actions)
        assert len(client.get("/api/v1/positions", headers=headers).json()) == 0

        trades = client.get("/api/v1/paper/trades", headers=headers).json()
        assert len(trades) == 1
        assert trades[0]["net_profit"] is not None and trades[0]["net_profit"] < 0  # PnL correctness: a stop-out is a loss

        titles = [n["title"] for n in client.get("/api/v1/notifications", headers=headers).json()]
        assert "Stop Loss Hit" in titles

    def test_take_profit_hit_closes_position_and_notifies(self, registered_user, db_session, live_price):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        live_price["price"] = Decimal("100")
        self._open_long_with_levels(client, headers, stop_loss=50, take_profit=110)

        live_price["price"] = Decimal("200")
        provider = _FlatProvider()
        provider.get_latest_price = lambda symbol: Decimal("200")
        result = PaperTradingService(db_session, market_data_provider=provider).run_tick(sid)

        assert any(a.action == "closed_take_profit" for a in result.actions)
        trades = client.get("/api/v1/paper/trades", headers=headers).json()
        assert len(trades) == 1
        assert trades[0]["net_profit"] > 0  # PnL correctness: a target hit on a long is a win

        titles = [n["title"] for n in client.get("/api/v1/notifications", headers=headers).json()]
        assert "Take Profit Hit" in titles

    def test_price_between_levels_leaves_position_open(self, registered_user, db_session, live_price):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        live_price["price"] = Decimal("100")
        self._open_long_with_levels(client, headers, stop_loss=50, take_profit=200)

        provider = _FlatProvider()
        provider.get_latest_price = lambda symbol: Decimal("100")  # unchanged, between the levels
        result = PaperTradingService(db_session, market_data_provider=provider).run_tick(sid)
        assert not any(a.action.startswith("closed") for a in result.actions)
        assert len(client.get("/api/v1/positions", headers=headers).json()) == 1

    def test_portfolio_balance_reflects_realized_pnl_after_sl_close(self, registered_user, db_session, live_price):
        """Portfolio calculations per the spec: balance moves by exactly
        the realized PnL of the stop-out, nothing more."""
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        live_price["price"] = Decimal("100")
        before = Decimal(client.get("/api/v1/portfolio", headers=headers).json()["balance"])
        self._open_long_with_levels(client, headers, stop_loss=90, take_profit=200)

        live_price["price"] = Decimal("50")
        provider = _FlatProvider()
        provider.get_latest_price = lambda symbol: Decimal("50")
        PaperTradingService(db_session, market_data_provider=provider).run_tick(sid)

        after = Decimal(client.get("/api/v1/portfolio", headers=headers).json()["balance"])
        trade_pnl = Decimal(str(client.get("/api/v1/paper/trades", headers=headers).json()[0]["net_profit"]))
        assert after - before == trade_pnl


# ----------------------------------------------------------------------
# Data feed interruption & session recovery
# ----------------------------------------------------------------------

class TestDataFeedAndRecovery:
    def test_broken_feed_flags_tick_and_notifies(self, registered_user, db_session):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        result = PaperTradingService(db_session, market_data_provider=_BrokenProvider()).run_tick(sid)

        assert result.data_feed_ok is False
        assert len(result.rejections) == 1
        titles = [n["title"] for n in client.get("/api/v1/notifications", headers=headers).json()]
        assert "Data Feed Interrupted" in titles

    def test_broken_feed_does_not_kill_the_session(self, registered_user, db_session):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = _start_session(client, headers, strat["id"])["id"]

        PaperTradingService(db_session, market_data_provider=_BrokenProvider()).run_tick(uuid.UUID(sid))
        assert client.get(f"/api/v1/paper/sessions/{sid}", headers=headers).json()["status"] == "running"

    def test_running_sessions_marked_interrupted_on_recovery(self, registered_user, db_session):
        """Spec Testing Requirements: 'Session recovery after interruption'.
        Simulates an unclean shutdown (session left 'running') and verifies
        the startup pass marks it interrupted with an explanatory reason
        rather than silently resuming."""
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = _start_session(client, headers, strat["id"])["id"]

        marked = mark_interrupted_sessions(db_session)
        assert marked == 1

        session = client.get(f"/api/v1/paper/sessions/{sid}", headers=headers).json()
        assert session["status"] == "interrupted"
        assert "restarted" in session["status_reason"].lower()

    def test_recovery_pass_ignores_cleanly_stopped_sessions(self, registered_user, db_session):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = _start_session(client, headers, strat["id"])["id"]
        client.post("/api/v1/paper/stop", json={"session_id": sid}, headers=headers)

        assert mark_interrupted_sessions(db_session) == 0
        assert client.get(f"/api/v1/paper/sessions/{sid}", headers=headers).json()["status"] == "stopped"


# ----------------------------------------------------------------------
# Strategy monitoring
# ----------------------------------------------------------------------

class TestStrategyMonitoring:
    def test_monitor_reflects_real_trades_and_position(self, registered_user, db_session, live_price):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        PaperTradingService(db_session, market_data_provider=_TrendProvider()).run_tick(sid)

        monitors = client.get(f"/api/v1/paper/sessions/{sid}/monitor", headers=headers).json()
        assert len(monitors) == 1
        m = monitors[0]
        assert m["current_position"] == "long"
        assert m["status"] == "running"
        assert m["number_of_trades"] == 0  # position opened but not yet closed -> no completed trades

    def test_monitor_win_rate_after_profitable_close(self, registered_user, db_session, live_price):
        client, headers, _ = registered_user
        strat = _make_enabled_strategy(client, headers)
        sid = uuid.UUID(_start_session(client, headers, strat["id"])["id"])

        # Tick 1: opens a long at ~110 (signal TP = 110 * 1.16 = 127.6)
        PaperTradingService(db_session, market_data_provider=_TrendProvider()).run_tick(sid)

        # The market rockets: monitoring sees the TP breached, and the
        # close fills at the new (profitable) live price.
        live_price["price"] = Decimal("200")
        winning = _TrendProvider(latest_price=Decimal("200"))
        PaperTradingService(db_session, market_data_provider=winning).run_tick(sid)

        monitors = client.get(f"/api/v1/paper/sessions/{sid}/monitor", headers=headers).json()
        m = monitors[0]
        assert m["number_of_trades"] == 1
        assert m["win_rate"] == 100.0
        assert m["running_pnl"] > 0
