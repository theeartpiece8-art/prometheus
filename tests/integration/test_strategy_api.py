class TestStrategyCRUD:
    def test_create_strategy(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/strategies",
            json={
                "name": "Test Strategy", "strategy_type": "moving_average_crossover",
                "parameters": {"fast_period": 10, "slow_period": 30},
            },
            headers=headers,
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "draft"
        assert resp.json()["parameters"]["fast_period"] == 10

    def test_create_strategy_with_invalid_parameters_is_rejected(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/strategies",
            json={
                "name": "Bad Strategy", "strategy_type": "moving_average_crossover",
                "parameters": {"fast_period": 50, "slow_period": 10},  # fast >= slow, invalid
            },
            headers=headers,
        )
        assert resp.status_code == 422

    def test_create_strategy_with_unknown_type_is_rejected(self, registered_user):
        client, headers, _ = registered_user
        resp = client.post(
            "/api/v1/strategies",
            json={"name": "Unknown", "strategy_type": "quantum_alpha_v9", "parameters": {}},
            headers=headers,
        )
        assert resp.status_code == 422

    def test_list_strategies_only_returns_own_strategies(self, client):
        client.post(
            "/api/v1/auth/register", json={"username": "user_a", "email": "a@x.com", "password": "S3curePass123"}
        )
        r1 = client.post("/api/v1/auth/login", json={"email": "a@x.com", "password": "S3curePass123"})
        headers_a = {"Authorization": f"Bearer {r1.json()['access_token']}"}
        client.post(
            "/api/v1/strategies",
            json={"name": "A's Strategy", "strategy_type": "moving_average_crossover", "parameters": {}},
            headers=headers_a,
        )

        client.post(
            "/api/v1/auth/register", json={"username": "user_b", "email": "b@x.com", "password": "S3curePass123"}
        )
        r2 = client.post("/api/v1/auth/login", json={"email": "b@x.com", "password": "S3curePass123"})
        headers_b = {"Authorization": f"Bearer {r2.json()['access_token']}"}

        resp_a = client.get("/api/v1/strategies", headers=headers_a)
        resp_b = client.get("/api/v1/strategies", headers=headers_b)
        assert len(resp_a.json()) == 1
        assert len(resp_b.json()) == 0

    def test_cannot_access_another_users_strategy(self, client):
        client.post(
            "/api/v1/auth/register", json={"username": "owner", "email": "owner@x.com", "password": "S3curePass123"}
        )
        r1 = client.post("/api/v1/auth/login", json={"email": "owner@x.com", "password": "S3curePass123"})
        headers_owner = {"Authorization": f"Bearer {r1.json()['access_token']}"}
        strat = client.post(
            "/api/v1/strategies",
            json={"name": "Private", "strategy_type": "moving_average_crossover", "parameters": {}},
            headers=headers_owner,
        ).json()

        client.post(
            "/api/v1/auth/register", json={"username": "intruder", "email": "intruder@x.com", "password": "S3curePass123"}
        )
        r2 = client.post("/api/v1/auth/login", json={"email": "intruder@x.com", "password": "S3curePass123"})
        headers_intruder = {"Authorization": f"Bearer {r2.json()['access_token']}"}

        resp = client.get(f"/api/v1/strategies/{strat['id']}", headers=headers_intruder)
        assert resp.status_code == 404

    def test_enable_disable_strategy(self, registered_user):
        client, headers, _ = registered_user
        strat = client.post(
            "/api/v1/strategies",
            json={"name": "Toggle Me", "strategy_type": "moving_average_crossover", "parameters": {}},
            headers=headers,
        ).json()

        resp = client.post(f"/api/v1/strategies/{strat['id']}/enable", headers=headers)
        assert resp.json()["status"] == "active"

        resp = client.post(f"/api/v1/strategies/{strat['id']}/disable", headers=headers)
        assert resp.json()["status"] == "disabled"

    def test_clone_strategy(self, registered_user):
        client, headers, _ = registered_user
        strat = client.post(
            "/api/v1/strategies",
            json={"name": "Original", "strategy_type": "moving_average_crossover", "parameters": {"fast_period": 5, "slow_period": 15}},
            headers=headers,
        ).json()
        clone = client.post(f"/api/v1/strategies/{strat['id']}/clone", headers=headers)
        assert clone.status_code == 201
        assert clone.json()["id"] != strat["id"]
        assert "copy" in clone.json()["name"]
        assert clone.json()["parameters"]["fast_period"] == 5

    def test_delete_strategy(self, registered_user):
        client, headers, _ = registered_user
        strat = client.post(
            "/api/v1/strategies",
            json={"name": "Delete Me", "strategy_type": "moving_average_crossover", "parameters": {}},
            headers=headers,
        ).json()
        resp = client.delete(f"/api/v1/strategies/{strat['id']}", headers=headers)
        assert resp.status_code == 200

        resp = client.get(f"/api/v1/strategies/{strat['id']}", headers=headers)
        assert resp.status_code == 404
