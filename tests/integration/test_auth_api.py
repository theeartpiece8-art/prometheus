"""Integration tests for the authentication API, per 13_Testing_Strategy.md's
'API Testing' requirements (endpoint correctness, auth enforcement, input validation)."""


class TestRegistration:
    def test_register_creates_user_and_returns_tokens(self, client):
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "new_trader", "email": "new@example.com", "password": "S3curePass123"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["user"]["username"] == "new_trader"
        assert body["user"]["email"] == "new@example.com"
        assert body["user"]["role"] == "trader"
        assert "access_token" in body
        assert "refresh_token" in body

    def test_register_creates_default_portfolio(self, registered_user):
        client, headers, _ = registered_user
        resp = client.get("/api/v1/portfolio", headers=headers)
        assert resp.status_code == 200
        assert float(resp.json()["balance"]) == 10000.0

    def test_duplicate_email_is_rejected(self, client):
        payload = {"username": "user_one", "email": "dupe@example.com", "password": "S3curePass123"}
        r1 = client.post("/api/v1/auth/register", json=payload)
        assert r1.status_code == 201

        payload2 = {"username": "user_two", "email": "dupe@example.com", "password": "S3curePass123"}
        r2 = client.post("/api/v1/auth/register", json=payload2)
        assert r2.status_code == 409

    def test_duplicate_username_is_rejected(self, client):
        payload = {"username": "same_name", "email": "a@example.com", "password": "S3curePass123"}
        client.post("/api/v1/auth/register", json=payload)

        payload2 = {"username": "same_name", "email": "b@example.com", "password": "S3curePass123"}
        resp = client.post("/api/v1/auth/register", json=payload2)
        assert resp.status_code == 409

    def test_weak_password_is_rejected(self, client):
        resp = client.post(
            "/api/v1/auth/register", json={"username": "weak_pw", "email": "weak@example.com", "password": "short"}
        )
        assert resp.status_code == 422

    def test_invalid_email_is_rejected(self, client):
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "bad_email", "email": "not-an-email", "password": "S3curePass123"},
        )
        assert resp.status_code == 422


class TestLogin:
    def test_login_with_correct_credentials_succeeds(self, client):
        client.post(
            "/api/v1/auth/register",
            json={"username": "login_user", "email": "login@example.com", "password": "S3curePass123"},
        )
        resp = client.post("/api/v1/auth/login", json={"email": "login@example.com", "password": "S3curePass123"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_login_with_wrong_password_fails(self, client):
        client.post(
            "/api/v1/auth/register",
            json={"username": "login_user2", "email": "login2@example.com", "password": "S3curePass123"},
        )
        resp = client.post("/api/v1/auth/login", json={"email": "login2@example.com", "password": "WrongPassword1"})
        assert resp.status_code == 401

    def test_login_with_nonexistent_email_fails(self, client):
        resp = client.post(
            "/api/v1/auth/login", json={"email": "doesnotexist@example.com", "password": "S3curePass123"}
        )
        assert resp.status_code == 401


class TestProtectedRoutes:
    def test_profile_requires_authentication(self, client):
        resp = client.get("/api/v1/auth/profile")
        assert resp.status_code in (401, 403)  # HTTPBearer with auto_error returns 403 if header entirely missing

    def test_profile_rejects_garbage_token(self, client):
        resp = client.get("/api/v1/auth/profile", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401

    def test_profile_returns_current_user(self, registered_user):
        client, headers, body = registered_user
        resp = client.get("/api/v1/auth/profile", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["id"] == body["user"]["id"]


class TestRefreshAndLogout:
    def test_refresh_issues_new_access_token(self, client):
        register = client.post(
            "/api/v1/auth/register",
            json={"username": "refresh_user", "email": "refresh@example.com", "password": "S3curePass123"},
        )
        refresh_token = register.json()["refresh_token"]
        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_refresh_with_access_token_instead_of_refresh_fails(self, client):
        register = client.post(
            "/api/v1/auth/register",
            json={"username": "confused_user", "email": "confused@example.com", "password": "S3curePass123"},
        )
        access_token = register.json()["access_token"]
        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
        assert resp.status_code == 401

    def test_logout_revokes_refresh_token(self, client):
        register = client.post(
            "/api/v1/auth/register",
            json={"username": "logout_user", "email": "logout@example.com", "password": "S3curePass123"},
        )
        refresh_token = register.json()["refresh_token"]

        logout_resp = client.post("/api/v1/auth/logout", json={"refresh_token": refresh_token})
        assert logout_resp.status_code == 200

        # The revoked refresh token must no longer be usable.
        resp = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 401
