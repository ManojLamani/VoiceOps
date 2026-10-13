"""Test configuration.

Runs against a throwaway SQLite database by default; set TEST_DATABASE_URL to run the same
suite against PostgreSQL. External AI services are disabled: the agent runs its offline
policy unless a test injects a scripted LLM, and STT/TTS use injected fakes.
"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="voiceops-test-")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_tmp}/test.db")
os.environ.update({
    "LLM_PROVIDER": "offline", "LLM_BASE_URL": "", "LLM_API_KEY": "",
    "STT_PROVIDER": "browser", "TTS_PROVIDER": "browser",
    "AUTO_SEED": "true", "JWT_SECRET": "test-secret-key-that-is-long-enough-for-hs256",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db.seed import seed_database  # noqa: E402
from app.services import llm_service, voice_service  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh_db(client):
    """Every test starts from the same seeded data and with AI providers reset."""
    client.portal.call(seed_database, True)
    llm_service.set_llm_provider(None)
    voice_service.set_voice_providers(None, None)
    yield


@pytest.fixture
def run(client):
    """Run an async function on the app's event loop (same loop as the DB engine)."""
    def _run(fn, *args, **kwargs):
        async def call():
            return await fn(*args, **kwargs)
        return client.portal.call(call)
    return _run


def _login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def admin_headers(client):
    return _login(client, "admin@voiceops.ai", "admin123")


@pytest.fixture
def agent_headers(client):
    return _login(client, "agent@voiceops.ai", "agent123")


@pytest.fixture
def start_conversation(client, admin_headers):
    def _start(customer_id=None):
        r = client.post("/api/conversations", json={"customer_id": customer_id, "channel": "text"},
                        headers=admin_headers)
        assert r.status_code == 201, r.text
        return r.json()["id"]
    return _start


@pytest.fixture
def say(client, admin_headers):
    def _say(conversation_id, text):
        r = client.post(f"/api/conversations/{conversation_id}/messages", json={"content": text},
                        headers=admin_headers)
        assert r.status_code == 200, r.text
        return r.json()
    return _say
