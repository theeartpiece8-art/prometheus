"""
Integration tests for the Backtesting API. Covers the full HTTP surface
plus two safety properties that matter most for Sprint 2:
  1. A backtest must never mutate the user's real (live/paper) portfolio,
     positions, or orders — it is a fully isolated simulation.
  2. Every simulated entry genuinely passes through the Risk Engine
     (proven here via a restrictive risk-setting update through the same
     API a real user would use, then confirming the backtest respects it).
"""
import datetime as dt
import uuid
from decimal import Decimal

import pytest

from app.application.schemas.backtest import BacktestRunRequest
from app.application.services.backtest_service import BacktestService
from app.infrastructure.market_data.base_provider import MarketDataProvider


def _create_backtestable_strategy(client, headers, **param_overrides) -> dict:
    params = {"fast_period": 5, "slow_period": 20, "stop_loss_pct": 8.0, "take_profit_pct": 16.0}
    params.update(param_overrides)
    resp = client.post(
        "/api/v1/strategies",
        json={"name": "Backtest Strategy", "strategy_type": "moving_average_crossover", "parameters": params},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _run_request(strategy_id: str, **overrides) -> dict:
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(days=365)
    payload = {
        "strategy_id": strategy_id, "symbol": "AAPL", "timeframe": "1D",
        "start_date": start.isoformat(), "end_date": end.isoformat(), "initial_balance": 10000,
    }
    payload.update(overrides)
    return payload


class TestBacktestRunHappyPath:
    def test_run_returns_completed_result_with_full_metrics(self, registered_user):
        client, headers, _ = registered_user
        strategy = _create_backtestable_strategy(client, headers)

        resp = client.post("/api/v1/backtest/run", json=_run_request(strategy["id"]), headers=headers)
        assert resp.status_code == 201, resp.text
        body = resp.json()

        assert body["status"] == "completed"
        assert body["data_source"] in ("yfinance", "mock")
        assert body["bars_processed"] > 0
        assert "total_trades" in body["metrics"]
        assert "sharpe_ratio" in body["metrics"]
        assert "sortino_ratio" in body["metrics"]
        assert isinstance(body["trades"], list)
        assert isinstance(body["equity_curve"], list)
        assert len(body["equity_curve"]) > 0

    def test_run_with_unknown_strategy_returns_404(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post("/api/v1/backtest/run", json=_run_request(str(uuid.uuid4())), headers=headers)
        assert resp.status_code == 404

    def test_run_requires_authentication(self, client):
        resp = client.post("/api/v1/backtest/run", json=_run_request(str(uuid.uuid4())))
        assert resp.status_code in (401, 403)

    def test_cannot_backtest_another_users_strategy(self, client):
        client.post("/api/v1/auth/register", json={"username": "owner2", "email": "owner2@x.com", "password": "S3curePass123"})
        r1 = client.post("/api/v1/auth/login", json={"email": "owner2@x.com", "password": "S3curePass123"})
        headers_owner = {"Authorization": f"Bearer {r1.json()['access_token']}"}
        strategy = _create_backtestable_strategy(client, headers_owner)

        client.post("/api/v1/auth/register", json={"username": "intruder2", "email": "intruder2@x.com", "password": "S3curePass123"})
        r2 = client.post("/api/v1/auth/login", json={"email": "intruder2@x.com", "password": "S3curePass123"})
        headers_intruder = {"Authorization": f"Bearer {r2.json()['access_token']}"}

        resp = client.post("/api/v1/backtest/run", json=_run_request(strategy["id"]), headers=headers_intruder)
        assert resp.status_code == 404

    def test_invalid_date_range_is_rejected_at_schema_level(self, registered_user):
        client, headers, _ = registered_user
        strategy = _create_backtestable_strategy(client, headers)
        end = dt.datetime.now(dt.timezone.utc)
        start = end + dt.timedelta(days=1)  # start AFTER end
        resp = client.post(
            "/api/v1/backtest/run",
            json=_run_request(strategy["id"], start_date=start.isoformat(), end_date=end.isoformat()),
            headers=headers,
        )
        assert resp.status_code == 422


class TestBacktestResultRetrieval:
    def test_results_endpoint_and_legacy_job_id_endpoint_return_identical_data(self, registered_user):
        client, headers, _ = registered_user
        strategy = _create_backtestable_strategy(client, headers)
        run_resp = client.post("/api/v1/backtest/run", json=_run_request(strategy["id"]), headers=headers)
        job_id = run_resp.json()["job_id"]

        r1 = client.get(f"/api/v1/backtest/results/{job_id}", headers=headers)
        r2 = client.get(f"/api/v1/backtest/{job_id}", headers=headers)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json() == r2.json()

    def test_results_for_nonexistent_job_returns_404(self, registered_user):
        client, headers, _ = registered_user
        resp = client.get(f"/api/v1/backtest/results/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404

    def test_cannot_view_another_users_backtest_results(self, client):
        client.post("/api/v1/auth/register", json={"username": "owner3", "email": "owner3@x.com", "password": "S3curePass123"})
        r1 = client.post("/api/v1/auth/login", json={"email": "owner3@x.com", "password": "S3curePass123"})
        headers_owner = {"Authorization": f"Bearer {r1.json()['access_token']}"}
        strategy = _create_backtestable_strategy(client, headers_owner)
        run_resp = client.post("/api/v1/backtest/run", json=_run_request(strategy["id"]), headers=headers_owner)
        job_id = run_resp.json()["job_id"]

        client.post("/api/v1/auth/register", json={"username": "intruder3", "email": "intruder3@x.com", "password": "S3curePass123"})
        r2 = client.post("/api/v1/auth/login", json={"email": "intruder3@x.com", "password": "S3curePass123"})
        headers_intruder = {"Authorization": f"Bearer {r2.json()['access_token']}"}

        resp = client.get(f"/api/v1/backtest/results/{job_id}", headers=headers_intruder)
        assert resp.status_code == 404

    def test_history_lists_completed_runs(self, registered_user):
        client, headers, _ = registered_user
        strategy = _create_backtestable_strategy(client, headers)
        client.post("/api/v1/backtest/run", json=_run_request(strategy["id"]), headers=headers)
        client.post("/api/v1/backtest/run", json=_run_request(strategy["id"], symbol="MSFT"), headers=headers)

        resp = client.get("/api/v1/backtest/history", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2
        assert all(j["status"] == "completed" for j in resp.json())

    def test_report_download_is_a_clear_501_not_a_crash(self, registered_user):
        client, headers, _ = registered_user
        resp = client.get(f"/api/v1/backtest/report/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 501


class TestBacktestIsolationFromLiveTrading:
    """The most important architectural property in Sprint 2: a backtest
    must never touch the user's real paper-trading state."""

    def test_backtest_does_not_change_live_portfolio_balance(self, registered_user):
        client, headers, _ = registered_user
        before = client.get("/api/v1/portfolio", headers=headers).json()

        strategy = _create_backtestable_strategy(client, headers)
        client.post("/api/v1/backtest/run", json=_run_request(strategy["id"]), headers=headers)

        after = client.get("/api/v1/portfolio", headers=headers).json()
        assert before["balance"] == after["balance"]
        assert before["equity"] == after["equity"]

    def test_backtest_does_not_create_live_positions(self, registered_user):
        client, headers, _ = registered_user
        strategy = _create_backtestable_strategy(client, headers)
        client.post("/api/v1/backtest/run", json=_run_request(strategy["id"]), headers=headers)

        positions = client.get("/api/v1/positions", headers=headers)
        assert len(positions.json()) == 0

    def test_backtest_does_not_create_live_orders(self, registered_user):
        client, headers, _ = registered_user
        strategy = _create_backtestable_strategy(client, headers)
        client.post("/api/v1/backtest/run", json=_run_request(strategy["id"]), headers=headers)

        orders = client.get("/api/v1/orders", headers=headers)
        assert len(orders.json()) == 0

    def test_backtest_respects_the_users_actual_configured_risk_settings(self, registered_user):
        """Proves the backtest reads the SAME risk settings a live order
        would use — restrict allowed_symbols via the real settings API,
        then confirm the backtest for a disallowed symbol trades nothing."""
        client, headers, _ = registered_user
        client.put("/api/v1/risk/settings", json={"allowed_symbols": ["MSFT"]}, headers=headers)

        strategy = _create_backtestable_strategy(client, headers)
        resp = client.post("/api/v1/backtest/run", json=_run_request(strategy["id"], symbol="AAPL"), headers=headers)

        assert resp.status_code == 201
        body = resp.json()
        assert body["metrics"]["total_trades"] == 0
        assert len(body["risk_rejections"]) >= 1
        assert "allowed symbols" in body["risk_rejections"][0]["reason"].lower()


class TestBacktestServiceGuards:
    """Service-level tests for failure paths that are impractical to
    trigger honestly through the mock/yfinance providers over HTTP (the
    mock generator caps itself at 500 bars; a >5000-bar response can only
    realistically come from a live yfinance fetch, which this sandbox's
    network policy blocks). Testing directly against the service with an
    injected provider double exercises the actual guard logic."""

    def test_excessive_bar_count_is_rejected_not_silently_truncated(self, db_session, registered_user):
        client, headers, body = registered_user
        strategy = _create_backtestable_strategy(client, headers)

        class _HugeBarCountProvider(MarketDataProvider):
            name = "huge"

            def get_historical_ohlcv(self, symbol, timeframe, start_date, end_date):
                return [
                    {"timestamp": (start_date + dt.timedelta(minutes=i)).isoformat(),
                     "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000}
                    for i in range(6000)
                ]

            def get_latest_price(self, symbol):
                return Decimal("100")

        service = BacktestService(db_session, market_data_provider=_HugeBarCountProvider())
        request = BacktestRunRequest(**_run_request(strategy["id"]))
        job = service.run_backtest(uuid.UUID(body["user"]["id"]), request)

        assert job.status == "failed"
        assert "exceeding" in job.error_message.lower()

    def test_unknown_strategy_type_fails_gracefully_not_a_500(self, db_session, registered_user):
        client, headers, body = registered_user
        strategy = _create_backtestable_strategy(client, headers)

        # Directly corrupt the persisted strategy_type to simulate data that
        # somehow bypassed normal StrategyService validation.
        from app.infrastructure.models.strategy import Strategy

        row = db_session.get(Strategy, uuid.UUID(strategy["id"]))
        row.parameters = {**row.parameters, "_strategy_type": "nonexistent_strategy_v99"}
        db_session.commit()

        service = BacktestService(db_session)
        request = BacktestRunRequest(**_run_request(strategy["id"]))
        job = service.run_backtest(uuid.UUID(body["user"]["id"]), request)

        assert job.status == "failed"
        assert "unknown strategy_type" in job.error_message.lower()
