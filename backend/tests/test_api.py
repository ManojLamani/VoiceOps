"""REST endpoints, analytics from real data, and the WebSocket voice pipeline."""
import base64
import io
import wave

import pytest
from starlette.websockets import WebSocketDisconnect

from app.services import voice_service
from app.services.voice_service import VoiceError


def _wav(seconds=1.0, rate=16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x10\x00" * int(seconds * rate))
    return buf.getvalue()


class FakeSTT:
    name = "fake-stt"

    def __init__(self, text="What is the status of my order ORD001?", fail=False):
        self.text, self.fail, self.calls = text, fail, 0

    async def transcribe(self, audio, filename="audio.wav"):
        self.calls += 1
        if self.fail:
            raise VoiceError("decoder exploded")
        assert audio[:4] == b"RIFF"
        return {"text": self.text}


class FakeTTS:
    name = "fake-tts"

    def __init__(self, fail=False):
        self.fail, self.texts = fail, []

    async def synthesize(self, text):
        if self.fail:
            raise VoiceError("tts down")
        self.texts.append(text)
        return {"audio": b"ID3fake-mp3", "mime": "audio/mpeg"}


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["database"] == "up" and r.json()["rag_chunks"] > 50


def test_system_status(client, admin_headers):
    s = client.get("/api/system/status", headers=admin_headers).json()
    assert s["llm"]["mode"] == "offline" and s["rag"]["chunks"] > 50 and s["database"]["up"]


def test_customer_order_ticket_endpoints(client, admin_headers):
    customers = client.get("/api/customers", headers=admin_headers).json()
    assert len(customers) == 10
    emily = client.get("/api/customers/cust002", headers=admin_headers).json()
    assert emily["name"] == "Emily Chen"
    orders = client.get("/api/customers/CUST002/orders", headers=admin_headers).json()
    assert {o["order_id"] for o in orders} == {"ORD003", "ORD004"}
    assert next(o for o in orders if o["order_id"] == "ORD003")["can_cancel"] is True

    all_orders = client.get("/api/orders", headers=admin_headers).json()
    assert len(all_orders) == 14 and all(o["customer_code"] for o in all_orders)
    assert client.get("/api/orders/ORD404", headers=admin_headers).status_code == 404
    shipped = client.post("/api/orders/ORD001/cancel", json={"reason": "test"}, headers=admin_headers)
    assert shipped.status_code == 400

    t = client.post("/api/tickets", json={"customer_id": "CUST003", "subject": "Hi", "description": "Help"},
                    headers=admin_headers)
    assert t.status_code == 201 and t.json()["ticket_id"] == "TKT0005"
    upd = client.put("/api/tickets/TKT0005", json={"status": "resolved"}, headers=admin_headers)
    assert upd.json()["status"] == "resolved" and upd.json()["resolved_at"]
    assert client.put("/api/tickets/TKT0005", json={"status": "bogus"}, headers=admin_headers).status_code == 422


def test_analytics_are_computed_from_real_conversations(client, admin_headers, start_conversation, say):
    empty = client.get("/api/analytics/overview", headers=admin_headers).json()
    assert empty["total_conversations"] == 0 and empty["total_tool_calls"] == 0  # nothing faked by the seed

    c1 = start_conversation()
    say(c1, "status of ORD001 please")
    client.post(f"/api/conversations/{c1}/end", headers=admin_headers)
    c2 = start_conversation("CUST008")
    say(c2, "get me a human agent right now")
    client.post(f"/api/conversations/{c2}/end", headers=admin_headers)

    o = client.get("/api/analytics/overview", headers=admin_headers).json()
    assert o["total_conversations"] == 2 and o["resolved_conversations"] == 1 and o["escalated_conversations"] == 1
    assert o["resolution_rate"] == 50.0 and o["total_tool_calls"] == 2 and o["tool_success_rate"] == 100.0
    assert o["avg_response_time_ms"] > 0 and o["avg_duration_seconds"] >= 0

    series = client.get("/api/analytics/conversations-over-time?days=7", headers=admin_headers).json()
    assert len(series) == 7 and series[-1]["conversations"] == 2
    tools = {t["tool_name"]: t for t in client.get("/api/analytics/tool-usage", headers=admin_headers).json()}
    assert tools["get_order_status"]["success_count"] == 1
    assert len(client.get("/api/analytics/latency", headers=admin_headers).json()) == 2
    reasons = client.get("/api/analytics/escalation-reasons", headers=admin_headers).json()
    assert reasons == [{"reason": "customer_request", "count": 1}]

    listing = client.get("/api/conversations?status=escalated", headers=admin_headers).json()
    assert listing["total"] == 1 and listing["conversations"][0]["customer_code"] == "CUST008"


def test_escalation_status_update(client, admin_headers, start_conversation, say):
    conv = start_conversation("CUST002")
    say(conv, "I need to talk to a human")
    esc = client.get("/api/escalations", headers=admin_headers).json()[0]
    r = client.patch(f"/api/escalations/{esc['id']}", json={"status": "resolved"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "resolved" and r.json()["resolved_at"]


def test_rest_voice_respond(client, admin_headers, start_conversation):
    stt, tts = FakeSTT(), FakeTTS()
    voice_service.set_voice_providers(stt, tts)
    conv = start_conversation()
    r = client.post("/api/voice/respond", data={"conversation_id": conv},
                    files={"audio": ("u.wav", _wav(), "audio/wav")}, headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["transcript"].startswith("What is the status")
    assert body["tool_calls"][0]["tool_name"] == "get_order_status"
    assert base64.b64decode(body["audio_base64"]) == b"ID3fake-mp3" and body["timings"]["total_ms"] > 0
    assert tts.texts == [body["content"]]


def test_rest_voice_tts_failure_still_returns_text(client, admin_headers, start_conversation):
    voice_service.set_voice_providers(FakeSTT(), FakeTTS(fail=True))
    conv = start_conversation()
    r = client.post("/api/voice/respond", data={"conversation_id": conv, "text": "status of ORD002"},
                    headers=admin_headers).json()
    assert r["audio_base64"] is None and "ORD002" in r["content"]


def _token(headers):
    return headers["Authorization"].split()[1]


def _collect_until(ws, terminal):
    events = []
    while True:
        msg = ws.receive_json()
        events.append(msg)
        if msg["type"] in terminal:
            return events


def test_websocket_voice_turn_event_sequence(client, admin_headers, start_conversation):
    voice_service.set_voice_providers(FakeSTT(), FakeTTS())
    conv = start_conversation()
    with client.websocket_connect(f"/api/voice/ws/{conv}") as ws:
        ws.send_json({"type": "auth", "token": _token(admin_headers)})
        assert ws.receive_json()["type"] == "READY"
        ws.send_json({"type": "audio", "data": base64.b64encode(_wav()).decode()})
        events = _collect_until(ws, {"COMPLETED", "ERROR"})
        types = [e["type"] for e in events]
        expected = ["TRANSCRIBING", "TRANSCRIPT", "INTENT_DETECTED", "RETRIEVING_KNOWLEDGE", "KNOWLEDGE_RETRIEVED",
                    "TOOL_CALL_STARTED", "TOOL_CALL_COMPLETED", "AGENT_RESPONSE", "SPEAKING", "COMPLETED"]
        assert [t for t in types if t in expected] == expected
        response = next(e for e in events if e["type"] == "AGENT_RESPONSE")["data"]
        assert "ORD001" in response["content"]
        assert next(e for e in events if e["type"] == "INTENT_DETECTED")["data"]["intent"] == "order_status"

        ws.send_json({"type": "end"})
        ended = _collect_until(ws, {"CONVERSATION_ENDED"})[-1]["data"]
        assert ended["status"] == "completed" and ended["summary"]


def test_websocket_confirmation_and_escalation_events(client, admin_headers, start_conversation):
    voice_service.set_voice_providers(None, FakeTTS())
    conv = start_conversation("CUST002")
    with client.websocket_connect(f"/api/voice/ws/{conv}") as ws:
        ws.send_json({"type": "auth", "token": _token(admin_headers)})
        ws.receive_json()
        ws.send_json({"type": "text", "content": "cancel my order please"})
        types = [e["type"] for e in _collect_until(ws, {"COMPLETED"})]
        assert "CONFIRMATION_REQUIRED" in types
        ws.send_json({"type": "text", "content": "I want a human"})
        events = _collect_until(ws, {"COMPLETED"})
        assert "ESCALATED" in [e["type"] for e in events] and events[-1]["data"]["status"] == "escalated"


def test_websocket_stt_failures_are_reported(client, admin_headers, start_conversation):
    voice_service.set_voice_providers(FakeSTT(fail=True), FakeTTS())
    conv = start_conversation()
    with client.websocket_connect(f"/api/voice/ws/{conv}") as ws:
        ws.send_json({"type": "auth", "token": _token(admin_headers)})
        ws.receive_json()
        ws.send_json({"type": "audio", "data": base64.b64encode(_wav()).decode()})
        events = _collect_until(ws, {"STT_FAILED"})
        assert events[-1]["data"]["retry"] is True
        ws.send_json({"type": "audio", "data": "%%%not-base64%%%"})
        assert _collect_until(ws, {"STT_FAILED"})[-1]["type"] == "STT_FAILED"

    voice_service.set_voice_providers(FakeSTT(text=""), FakeTTS())
    conv = start_conversation()
    with client.websocket_connect(f"/api/voice/ws/{conv}") as ws:
        ws.send_json({"type": "auth", "token": _token(admin_headers)})
        ws.receive_json()
        ws.send_json({"type": "audio", "data": base64.b64encode(_wav()).decode()})
        assert _collect_until(ws, {"NO_SPEECH", "COMPLETED"})[-1]["type"] == "NO_SPEECH"


def test_websocket_rejects_bad_auth(client, start_conversation):
    conv = start_conversation()
    with client.websocket_connect(f"/api/voice/ws/{conv}") as ws:
        ws.send_json({"type": "auth", "token": "forged"})
        assert ws.receive_json()["type"] == "AUTH_FAILED"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_stale_conversations_are_closed_with_last_activity_time(client, admin_headers, start_conversation, say, run):
    from datetime import datetime, timedelta
    from app.services.maintenance import end_stale_conversations

    talked = start_conversation()
    say(talked, "status of ORD001 please")
    silent = start_conversation()
    assert run(end_stale_conversations, 30) == 0  # fresh conversations are left alone

    later = datetime.utcnow() + timedelta(minutes=31)
    assert run(end_stale_conversations, 30, later) == 2
    a = client.get(f"/api/conversations/{talked}", headers=admin_headers).json()["conversation"]
    b = client.get(f"/api/conversations/{silent}", headers=admin_headers).json()["conversation"]
    assert a["status"] == "completed" and a["resolution"] == "resolved" and a["summary"]
    assert a["duration_seconds"] < 60  # ended at last activity, not at sweep time
    assert b["status"] == "abandoned"
