"""
Tests for the manual Emergency Kill Switch API (Sprint 4 module 10):
POST /risk/kill-switch (with and without close_positions) and
POST /risk/kill-switch/reset. The underlying mechanism is Sprint 1's --
these prove the new API surface and its interactions: idempotent
activation, close-all actually closing through the real pipeline, reset
re-enabling trading, and the audit trail.
"""
from decimal import Decimal

import pytest


def _open_position(client, headers, symbol="AAPL"):
    r = client.post(
        "/api/v1/orders/place",
        json={"symbol": symbol, "side": "buy", "order_type": "market", "stop_loss": 90},
        headers=headers,
    )
    assert r.json()["order"]["status"] == "filled", r.text


class TestKillSwitchActivation:
    def test_activate_blocks_new_orders(self, registered_user, live_price):
        client, headers, _ = registered_user
        r = client.post("/api/v1/risk/kill-switch", json={"reason": "Testing emergency stop"}, headers=headers)
        assert r.status_code == 200
        assert r.json()["kill_switch_active"] is True

        # Risk status reflects it
        status = client.get("/api/v1/risk/status", headers=headers).json()
        assert status["kill_switch_active"] is True

        # New orders are rejected by the Risk Engine
        order = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90},
            headers=headers,
        ).json()["order"]
        assert order["status"] == "rejected"
        assert "kill switch" in order["rejection_reason"].lower()

    def test_activate_without_close_flag_preserves_positions(self, registered_user, live_price):
        client, headers, _ = registered_user
        _open_position(client, headers)

        r = client.post("/api/v1/risk/kill-switch", json={}, headers=headers)
        assert r.json()["positions_closed"] == 0
        assert len(client.get("/api/v1/positions", headers=headers).json()) == 1

    def test_activate_with_close_flag_closes_all_positions(self, registered_user, live_price):
        client, headers, _ = registered_user
        _open_position(client, headers, "AAPL")
        _open_position(client, headers, "MSFT")
        assert len(client.get("/api/v1/positions", headers=headers).json()) == 2

        r = client.post("/api/v1/risk/kill-switch", json={"close_positions": True}, headers=headers)
        assert r.json()["positions_closed"] == 2
        assert client.get("/api/v1/positions", headers=headers).json() == []

        # The closes produced real trades with the emergency reason in notifications
        notifications = client.get("/api/v1/notifications", headers=headers).json()
        assert any("Kill Switch: Emergency Close" in n["title"] for n in notifications)

    def test_activation_is_idempotent_and_audited(self, registered_user):
        client, headers, _ = registered_user
        client.post("/api/v1/risk/kill-switch", json={"reason": "first"}, headers=headers)
        r2 = client.post("/api/v1/risk/kill-switch", json={"reason": "second"}, headers=headers)
        assert r2.status_code == 200
        assert r2.json()["kill_switch_active"] is True

        events = client.get("/api/v1/risk/events", headers=headers).json()
        activations = [e for e in events if e["event_type"] == "kill_switch_activated"]
        assert len(activations) == 2  # both audited, even though state only changed once

    def test_default_reason_recorded_when_none_given(self, registered_user):
        client, headers, _ = registered_user
        client.post("/api/v1/risk/kill-switch", json={}, headers=headers)
        events = client.get("/api/v1/risk/events", headers=headers).json()
        assert any("manual emergency stop" in (e["description"] or "").lower() for e in events)


class TestKillSwitchReset:
    def test_reset_re_enables_trading(self, registered_user, live_price):
        client, headers, _ = registered_user
        client.post("/api/v1/risk/kill-switch", json={}, headers=headers)

        r = client.post("/api/v1/risk/kill-switch/reset", headers=headers)
        assert r.json()["kill_switch_active"] is False

        order = client.post(
            "/api/v1/orders/place",
            json={"symbol": "AAPL", "side": "buy", "order_type": "market", "stop_loss": 90},
            headers=headers,
        ).json()["order"]
        assert order["status"] == "filled"

    def test_reset_is_audited_as_a_risk_event(self, registered_user):
        client, headers, _ = registered_user
        client.post("/api/v1/risk/kill-switch", json={}, headers=headers)
        client.post("/api/v1/risk/kill-switch/reset", headers=headers)

        events = client.get("/api/v1/risk/events", headers=headers).json()
        assert any(e["event_type"] == "kill_switch_reset" for e in events)

    def test_endpoints_require_authentication(self, client):
        assert client.post("/api/v1/risk/kill-switch", json={}).status_code == 401
        assert client.post("/api/v1/risk/kill-switch/reset").status_code == 401
