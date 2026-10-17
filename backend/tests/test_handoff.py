"""Live human handoff: staff join an active conversation, talk to the customer, and hand back to the AI."""
from app.services import voice_service
from tests.test_api import FakeTTS, _collect_until, _token


def _join(client, conv, headers):
    return client.post(f"/api/conversations/{conv}/handoff/join", headers=headers)


def _reply(client, conv, headers, content):
    return client.post(f"/api/conversations/{conv}/handoff/messages", json={"content": content}, headers=headers)


def _leave(client, conv, headers):
    return client.post(f"/api/conversations/{conv}/handoff/leave", headers=headers)


def _escalation(client, headers):
    return client.get("/api/escalations", headers=headers).json()[0]


def test_human_takes_over_and_hands_back(client, admin_headers, agent_headers, start_conversation):
    tts = FakeTTS()
    voice_service.set_voice_providers(None, tts)
    conv = start_conversation("CUST002")
    with client.websocket_connect(f"/api/voice/ws/{conv}") as ws:
        ws.send_json({"type": "auth", "token": _token(admin_headers)})
        assert ws.receive_json()["data"]["human_agent"] is None
        ws.send_json({"type": "text", "content": "I need to talk to a human"})
        assert _collect_until(ws, {"COMPLETED"})[-1]["data"]["status"] == "escalated"

        joined = _join(client, conv, agent_headers)
        assert joined.status_code == 200 and joined.json()["human_agent"]["name"] == "Sarah Mitchell"
        assert ws.receive_json() == {"type": "HUMAN_JOINED", "data": {"agent_name": "Sarah Mitchell"}}
        assert _escalation(client, agent_headers)["status"] == "assigned"

        # The AI stays silent: the customer's message goes to the human.
        ws.send_json({"type": "text", "content": "Hi, can you check my refund?"})
        events = _collect_until(ws, {"COMPLETED"})
        assert [e["type"] for e in events] == ["TRANSCRIPT", "COMPLETED"]
        assert events[-1]["data"]["status"] == "with_human"

        reply = _reply(client, conv, agent_headers, "Hi Emily, I can see your refund is on its way.")
        assert reply.status_code == 201 and reply.json()["author_name"] == "Sarah Mitchell"
        message = ws.receive_json()
        assert message["type"] == "HUMAN_MESSAGE" and message["data"]["content"].startswith("Hi Emily")
        assert ws.receive_json()["type"] == "SPEAKING"
        assert tts.texts[-1] == "Hi Emily, I can see your refund is on its way."

        assert _leave(client, conv, agent_headers).status_code == 200
        assert ws.receive_json() == {"type": "HUMAN_LEFT", "data": {"agent_name": "Sarah Mitchell"}}
        assert _escalation(client, agent_headers)["status"] == "resolved"

        # The AI is back, and answers normally instead of repeating the hand-off message.
        ws.send_json({"type": "text", "content": "What is the status of order ORD003?"})
        response = next(e for e in _collect_until(ws, {"COMPLETED"}) if e["type"] == "AGENT_RESPONSE")
        assert "ORD003" in response["data"]["content"]

    detail = client.get(f"/api/conversations/{conv}", headers=agent_headers).json()
    turns = [(m["role"], m["author_name"]) for m in detail["messages"]]
    assert ("assistant", "Sarah Mitchell") in turns
    assert [m["content"] for m in detail["messages"] if m["role"] == "system"] == [
        "Sarah Mitchell joined the conversation.", "Sarah Mitchell handed the conversation back to Ava."]
    human = next(m for m in detail["messages"] if m["author_name"])
    assert human["author_id"] and human["processing_time_ms"] is None


def test_customer_asking_again_after_handback_reopens_escalation(client, admin_headers, agent_headers,
                                                                  start_conversation, say):
    conv = start_conversation("CUST002")
    say(conv, "I need to talk to a human")
    _join(client, conv, agent_headers)
    relayed = say(conv, "Thanks for picking up")  # REST turns are relayed to the human too
    assert relayed["status"] == "with_human" and relayed["content"] == ""
    _leave(client, conv, agent_headers)
    again = say(conv, "Actually I want to talk to a person again")
    assert again["status"] == "escalated"
    assert _escalation(client, agent_headers)["status"] == "pending"


def test_handoff_permissions_and_conflicts(client, admin_headers, agent_headers, start_conversation):
    conv = start_conversation()
    viewer = client.post("/api/auth/register", json={"email": "watcher@voiceops.ai", "password": "s3cure-pass",
                                                     "full_name": "Watcher"}).json()["access_token"]
    assert _join(client, conv, {"Authorization": f"Bearer {viewer}"}).status_code == 403
    assert _reply(client, conv, agent_headers, "hello").status_code == 409  # not joined yet
    assert _leave(client, conv, agent_headers).status_code == 409

    assert _join(client, conv, agent_headers).status_code == 200
    assert _join(client, conv, agent_headers).status_code == 200  # joining twice is harmless
    taken = _join(client, conv, admin_headers)
    assert taken.status_code == 409 and "Sarah Mitchell" in taken.json()["detail"]
    assert _reply(client, conv, admin_headers, "hello").status_code == 409
    assert _reply(client, conv, agent_headers, "").status_code == 422

    assert _leave(client, conv, admin_headers).status_code == 200  # an admin can release a stuck handoff
    client.post(f"/api/conversations/{conv}/end", headers=admin_headers)
    assert _join(client, conv, agent_headers).status_code == 409
    assert _join(client, "00000000-0000-0000-0000-000000000000", agent_headers).status_code == 404


def test_live_feed_streams_the_conversation(client, admin_headers, agent_headers, start_conversation, say):
    conv = start_conversation()
    with client.websocket_connect(f"/api/conversations/{conv}/live") as feed:
        feed.send_json({"type": "auth", "token": _token(agent_headers)})
        assert feed.receive_json() == {"type": "READY", "data": {"ended": False}}

        say(conv, "What is the status of order ORD001?")
        user, assistant = feed.receive_json(), feed.receive_json()
        assert user["type"] == assistant["type"] == "MESSAGE"
        assert user["data"]["role"] == "user" and assistant["data"]["role"] == "assistant"
        assert "ORD001" in assistant["data"]["content"] and assistant["data"]["author_name"] is None

        _join(client, conv, agent_headers)
        types = [feed.receive_json()["type"] for _ in range(2)]
        assert sorted(types) == ["HUMAN_JOINED", "MESSAGE"]

        client.post(f"/api/conversations/{conv}/end", headers=admin_headers)
        assert feed.receive_json()["type"] == "CONVERSATION_ENDED"


def test_live_feed_rejects_bad_auth(client, start_conversation):
    conv = start_conversation()
    with client.websocket_connect(f"/api/conversations/{conv}/live") as feed:
        feed.send_json({"type": "auth", "token": "forged"})
        assert feed.receive_json()["type"] == "AUTH_FAILED"
