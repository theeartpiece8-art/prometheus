class TestRiskStatusAPI:
    def test_risk_status_reflects_clean_account(self, registered_user):
        client, headers, _ = registered_user
        resp = client.get("/api/v1/risk/status", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert float(body["equity"]) == 10000.0
        assert body["kill_switch_active"] is False
        assert float(body["current_drawdown_pct"]) == 0.0
        # Regression guard for the Decimal scientific-notation bug found during
        # Sprint 1 development (Decimal('0') / Decimal('10000.0000') -> '0E+4').
        assert "E" not in body["leverage"]
        assert "E" not in body["current_drawdown_pct"]

    def test_risk_status_requires_authentication(self, client):
        resp = client.get("/api/v1/risk/status")
        assert resp.status_code in (401, 403)


class TestRiskSettings:
    def test_get_default_risk_settings(self, registered_user):
        client, headers, _ = registered_user
        resp = client.get("/api/v1/risk/settings", headers=headers)
        assert resp.status_code == 200
        assert float(resp.json()["risk_per_trade_pct"]) == 1.0

    def test_update_risk_settings_persists(self, registered_user):
        client, headers, _ = registered_user
        resp = client.put("/api/v1/risk/settings", json={"max_open_positions": 3}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["max_open_positions"] == 3

        # Confirm it actually persisted, not just echoed back.
        resp2 = client.get("/api/v1/risk/settings", headers=headers)
        assert resp2.json()["max_open_positions"] == 3

    def test_updated_risk_settings_are_enforced_by_the_engine(self, registered_user):
        """The critical end-to-end property: changing a risk setting via the
        API must actually change what the Risk Engine allows on the next order."""
        client, headers, _ = registered_user
        client.put("/api/v1/risk/settings", json={"max_open_positions": 1}, headers=headers)

        r1 = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90},
            headers=headers,
        )
        assert r1.json()["order"]["status"] == "filled"

        r2 = client.post(
            "/api/v1/orders/place",
            json={"symbol": "MSFT", "side": "buy", "order_type": "market", "stop_loss": 50},
            headers=headers,
        )
        assert r2.json()["order"]["status"] == "rejected"
        assert "open positions" in r2.json()["order"]["rejection_reason"].lower()


class TestOrderPreview:
    def test_preview_does_not_create_an_order(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/risk/preview",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["would_approve"] is True

        orders = client.get("/api/v1/orders", headers=headers)
        assert len(orders.json()) == 0  # preview must not persist an order

    def test_preview_does_not_pollute_risk_event_audit_log(self, registered_user):
        client, headers, _ = registered_user
        client.post(
            "/api/v1/risk/preview",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "quantity": 10_000_000},
            headers=headers,
        )
        events = client.get("/api/v1/risk/events", headers=headers)
        assert len(events.json()) == 0
