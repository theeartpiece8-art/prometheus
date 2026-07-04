class TestHealthAndVersion:
    def test_health_check_reports_healthy_with_working_db(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
        assert resp.json()["database"] == "healthy"

    def test_version_endpoint(self, client):
        resp = client.get("/api/v1/version")
        assert resp.status_code == 200
        assert "version" in resp.json()

    def test_root_endpoint(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_health_does_not_require_authentication(self, client):
        # Health checks must be reachable by load balancers / orchestrators
        # without credentials.
        resp = client.get("/api/v1/health")
        assert resp.status_code != 401
