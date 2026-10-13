def test_register_login_me(client):
    r = client.post("/api/auth/register", json={"email": "new.user@voiceops.ai", "password": "s3cure-pass",
                                                "full_name": "New User"})
    assert r.status_code == 201
    token = r.json()["access_token"]
    assert r.json()["user"]["role"] == "viewer"  # self-registered accounts are read-only

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "new.user@voiceops.ai"

    login = client.post("/api/auth/login", json={"email": "new.user@voiceops.ai", "password": "s3cure-pass"})
    assert login.status_code == 200


def test_register_rejects_duplicate_and_weak_password(client):
    assert client.post("/api/auth/register", json={"email": "admin@voiceops.ai", "password": "whatever123",
                                                   "full_name": "X"}).status_code == 400
    assert client.post("/api/auth/register", json={"email": "weak@voiceops.ai", "password": "short",
                                                   "full_name": "X"}).status_code == 422


def test_password_is_hashed(client, run):
    from sqlalchemy import select
    from app.db.database import async_session_factory
    from app.db.models import User

    async def fetch():
        async with async_session_factory() as s:
            return (await s.execute(select(User).where(User.email == "admin@voiceops.ai"))).scalar_one()
    user = run(fetch)
    assert user.password_hash != "admin123" and user.password_hash.startswith("$2")


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"email": "admin@voiceops.ai", "password": "nope-nope"})
    assert r.status_code == 401


def test_protected_routes_require_valid_token(client):
    assert client.get("/api/customers").status_code == 401
    assert client.get("/api/customers", headers={"Authorization": "Bearer not-a-token"}).status_code == 401
    assert client.get("/api/analytics/overview").status_code == 401


def test_admin_only_routes(client, agent_headers, admin_headers):
    assert client.post("/api/knowledge/ingest", headers=agent_headers).status_code == 403
    r = client.post("/api/knowledge/ingest", headers=admin_headers)
    assert r.status_code == 200 and r.json()["chunks"] > 0
