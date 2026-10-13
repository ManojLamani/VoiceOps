"""Viewer role, PII masking, staff-only writes, validation (4xx not 500) and message limits."""
import base64
import uuid

import pytest

from tests.test_api import FakeSTT, FakeTTS, _wav, _collect_until
from app.services import voice_service


@pytest.fixture
def viewer_headers(client):
    r = client.post("/api/auth/register", json={"email": f"viewer{uuid.uuid4().hex[:6]}@demo.dev",
                                                "password": "viewer-pass-1", "full_name": "Demo Viewer"})
    assert r.status_code == 201 and r.json()["user"]["role"] == "viewer"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_viewer_sees_masked_pii_staff_sees_full(client, viewer_headers, admin_headers):
    v = client.get("/api/customers/CUST002", headers=viewer_headers).json()
    assert v["email"] == "e***@email.com" and v["phone"] == "***-0102" and v["address"] == "(hidden)"
    assert v["name"] == "Emily Chen"  # names stay visible
    a = client.get("/api/customers/CUST002", headers=admin_headers).json()
    assert a["email"] == "emily.chen@email.com"
    orders = client.get("/api/orders", headers=viewer_headers).json()
    assert all(o["shipping_address"] in (None, "(hidden)") for o in orders)
    assert orders[0]["order_id"].startswith("ORD")  # business data intact


def test_viewer_cannot_write(client, viewer_headers):
    assert client.put("/api/customers/CUST002", json={"phone": "+1 555 999 0000"}, headers=viewer_headers).status_code == 403
    assert client.post("/api/orders/ORD003/cancel", json={}, headers=viewer_headers).status_code == 403
    assert client.post("/api/orders/ORD002/return", json={"reason": "x"}, headers=viewer_headers).status_code == 403
    assert client.put("/api/tickets/TKT0001", json={"status": "closed"}, headers=viewer_headers).status_code == 403
    assert client.post("/api/tickets", json={"customer_id": "CUST001", "subject": "s", "description": "d"},
                       headers=viewer_headers).status_code == 403
    assert client.patch(f"/api/escalations/{uuid.uuid4()}", json={"status": "resolved"},
                        headers=viewer_headers).status_code == 403


def test_viewer_agent_turn_and_conversation_detail_are_masked(client, viewer_headers, start_conversation):
    conv = client.post("/api/conversations", json={"channel": "text"}, headers=viewer_headers).json()["id"]
    r = client.post(f"/api/conversations/{conv}/messages", json={"content": "my email is emily.chen@email.com"},
                    headers=viewer_headers).json()
    assert r["tool_calls"][0]["tool_name"] == "get_customer"
    assert r["tool_calls"][0]["result"]["email"] == "e***@email.com"
    detail = client.get(f"/api/conversations/{conv}", headers=viewer_headers).json()
    assert "emily.chen@email.com" not in str(detail)


def test_viewer_websocket_events_are_masked(client, viewer_headers):
    voice_service.set_voice_providers(None, FakeTTS())
    conv = client.post("/api/conversations", json={"channel": "voice"}, headers=viewer_headers).json()["id"]
    with client.websocket_connect(f"/api/voice/ws/{conv}") as ws:
        ws.send_json({"type": "auth", "token": viewer_headers["Authorization"].split()[1]})
        ws.receive_json()
        ws.send_json({"type": "text", "content": "customer ID is CUST002"})
        events = _collect_until(ws, {"COMPLETED"})
    assert "emily.chen@email.com" not in str(events) and "e***@email.com" in str(events)


def test_registration_can_be_disabled(client, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "registration_enabled", False)
    r = client.post("/api/auth/register", json={"email": "no@demo.dev", "password": "password123", "full_name": "N"})
    assert r.status_code == 403


def test_default_jwt_secret_never_used_outside_development(monkeypatch):
    from app.core import config
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", config.DEFAULT_JWT_SECRET)
    config.get_settings.cache_clear()
    try:
        s = config.get_settings()
        assert s.jwt_secret != config.DEFAULT_JWT_SECRET and len(s.jwt_secret) >= 32
    finally:
        monkeypatch.undo()
        config.get_settings.cache_clear()


# ── validation returns 4xx instead of 500 ──

def test_customer_update_validation(client, admin_headers):
    assert client.put("/api/customers/CUST010", json={"email": "not-an-email"}, headers=admin_headers).status_code == 422
    dup = client.put("/api/customers/CUST010", json={"email": "james.wilson@email.com"}, headers=admin_headers)
    assert dup.status_code == 409
    ok = client.put("/api/customers/cust010", json={"email": "Marcus.Lee2@Email.com"}, headers=admin_headers)
    assert ok.status_code == 200 and ok.json()["email"] == "marcus.lee2@email.com"


def test_ticket_update_validation(client, admin_headers):
    assert client.put("/api/tickets/TKT0001", json={"assigned_to": str(uuid.uuid4())},
                      headers=admin_headers).status_code == 422
    assert client.put("/api/tickets/TKT0001", json={"priority": "whenever"}, headers=admin_headers).status_code == 422
    assert client.post("/api/tickets", json={"customer_id": "CUST001", "subject": "s", "description": "d",
                                             "priority": "asap"}, headers=admin_headers).status_code == 422


# ── message length limits are the same everywhere ──

def test_message_length_limits(client, admin_headers, start_conversation):
    conv = start_conversation()
    long = "x" * 2001
    assert client.post(f"/api/conversations/{conv}/messages", json={"content": long},
                       headers=admin_headers).status_code == 422
    assert client.post("/api/voice/respond", data={"conversation_id": conv, "text": long},
                       headers=admin_headers).status_code == 422
    voice_service.set_voice_providers(FakeSTT(), FakeTTS())
    with client.websocket_connect(f"/api/voice/ws/{conv}") as ws:
        ws.send_json({"type": "auth", "token": admin_headers["Authorization"].split()[1]})
        ws.receive_json()
        ws.send_json({"type": "text", "content": long})
        err = _collect_until(ws, {"ERROR"})[-1]
        assert "too long" in err["data"]["error"]
