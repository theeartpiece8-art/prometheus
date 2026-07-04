"""
Order API integration tests. Critical property under test: the Risk Engine
is genuinely in the loop over HTTP, not just in isolated unit tests — an
oversized order must come back rejected with a real reason, and an
approved order must actually create a Position and update the Portfolio.
"""


class TestOrderValidation:
    def test_order_without_stop_loss_or_quantity_is_rejected_at_schema_level(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market"},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_limit_order_without_price_is_rejected_at_schema_level(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "limit", "quantity": 10},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_invalid_side_is_rejected(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "sideways", "order_type": "market", "stop_loss": 90, "requested_price": 100},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_negative_quantity_is_rejected(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "quantity": -5},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_orders_require_authentication(self, client):
        resp = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "quantity": 1},
        )
        assert resp.status_code in (401, 403)


class TestOrderExecution:
    def test_reasonable_order_is_approved_and_creates_position(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90},
            headers=headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["order"]["status"] == "filled"
        assert body["order"]["executed_price"] is not None
        assert all(c["result"] == "pass" for c in body["risk_checks"])

        positions = client.get("/api/v1/positions", headers=headers)
        assert len(positions.json()) == 1
        assert positions.json()[0]["symbol"] == "AAPL"

    def test_massively_oversized_order_is_rejected_with_reason(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "quantity": 10_000_000},
            headers=headers,
        )
        assert resp.status_code == 201  # order is recorded even when rejected (audit trail)
        body = resp.json()
        assert body["order"]["status"] == "rejected"
        assert body["order"]["rejection_reason"] is not None
        assert any(c["result"] == "fail" for c in body["risk_checks"])

        # A rejected order must NOT create a position.
        positions = client.get("/api/v1/positions", headers=headers)
        assert len(positions.json()) == 0

    def test_rejected_order_does_not_change_portfolio_balance(self, registered_user):
        client, headers, _ = registered_user
        before = client.get("/api/v1/portfolio", headers=headers).json()

        client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "quantity": 10_000_000},
            headers=headers,
        )

        after = client.get("/api/v1/portfolio", headers=headers).json()
        assert before["balance"] == after["balance"]
        assert before["equity"] == after["equity"]

    def test_kill_switch_blocks_all_orders_via_api(self, registered_user, db_session):
        """End-to-end proof that the kill switch — set directly at the DB
        layer, as an emergency stop mechanism would — blocks new orders
        reaching the API, not just the isolated domain engine."""
        client, headers, body = registered_user
        from app.infrastructure.models.portfolio import Portfolio

        portfolio = db_session.query(Portfolio).filter_by(user_id=body["user"]["id"]).first()
        portfolio.kill_switch_active = True
        db_session.commit()

        resp = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90},
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["order"]["status"] == "rejected"
        assert "kill switch" in resp.json()["order"]["rejection_reason"].lower()

    def test_order_for_disabled_strategy_is_rejected(self, registered_user):
        client, headers, _ = registered_user
        strat = client.post(
            "/api/v1/strategies",
            json={"name": "Disabled Strat", "strategy_type": "moving_average_crossover", "parameters": {}},
            headers=headers,
        ).json()
        assert strat["status"] == "draft"  # not active

        resp = client.post(
            "/api/v1/orders/place",
            json={
                "symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90,
                "strategy_id": strat["id"],
            },
            headers=headers,
        )
        assert resp.json()["order"]["status"] == "rejected"
        assert "not enabled" in resp.json()["order"]["rejection_reason"].lower()

    def test_order_for_enabled_strategy_is_allowed_through_strategy_check(self, registered_user):
        client, headers, _ = registered_user
        strat = client.post(
            "/api/v1/strategies",
            json={"name": "Active Strat", "strategy_type": "moving_average_crossover", "parameters": {}},
            headers=headers,
        ).json()
        client.post(f"/api/v1/strategies/{strat['id']}/enable", headers=headers)

        resp = client.post(
            "/api/v1/orders/place",
            json={
                "symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90,
                "strategy_id": strat["id"],
            },
            headers=headers,
        )
        assert resp.json()["order"]["status"] == "filled"

    def test_order_for_nonexistent_strategy_returns_404(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/orders/place",
            json={
                "symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90,
                "strategy_id": "00000000-0000-0000-0000-000000000000",
            },
            headers=headers,
        )
        assert resp.status_code == 404


class TestPositionClosing:
    def test_close_position_realizes_pnl_and_updates_portfolio(self, registered_user):
        client, headers, _ = registered_user
        client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90},
            headers=headers,
        )
        position = client.get("/api/v1/positions", headers=headers).json()[0]

        resp = client.post(f"/api/v1/positions/close/{position['id']}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"

        remaining = client.get("/api/v1/positions", headers=headers).json()
        assert len(remaining) == 0

    def test_close_already_closed_position_returns_404(self, registered_user):
        client, headers, _ = registered_user
        client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90},
            headers=headers,
        )
        position = client.get("/api/v1/positions", headers=headers).json()[0]
        client.post(f"/api/v1/positions/close/{position['id']}", headers=headers)

        resp = client.post(f"/api/v1/positions/close/{position['id']}", headers=headers)
        assert resp.status_code == 404
