"""Agent conversation flows (offline policy) and LLM tool-loop guardrails (scripted LLM)."""
import pytest

from app.services import llm_service, agent_service
from app.services.llm_service import LLMResponse, LLMError


def _order_status(client, headers, order_id):
    return client.get(f"/api/orders/{order_id}", headers=headers).json()["status"]


def _tools(reply):
    return [(t["tool_name"], t["result"].get("error_code") or t["result"].get("status")) for t in reply["tool_calls"]]


# ── Required flows ──

def test_flow1_order_status(start_conversation, say):
    conv = start_conversation()
    r = say(conv, "What is the status of my order ORD001?")
    assert _tools(r) == [("get_order_status", "shipped")]
    assert "ORD001" in r["content"] and "shipped" in r["content"] and r["intent"] == "order_status"


def test_flow2_cancel_with_verification_and_confirmation(client, admin_headers, start_conversation, say):
    conv = start_conversation()
    r = say(conv, "I want to cancel my order.")
    assert r["tool_calls"] == [] and "order number" in r["content"].lower()

    r = say(conv, "It's order ORD003.")
    assert _tools(r) == [("cancel_order", "verification_required")]

    r = say(conv, "My email is emily.chen@email.com")
    assert _tools(r) == [("get_customer", None), ("cancel_order", "confirmation_required")]
    assert r["status"] == "awaiting_confirmation" and r["pending_action"]["tool"] == "cancel_order"
    assert _order_status(client, admin_headers, "ORD003") == "confirmed"  # still untouched

    r = say(conv, "Yes, please go ahead.")
    assert _tools(r) == [("cancel_order", "cancelled")]
    assert _order_status(client, admin_headers, "ORD003") == "cancelled"

    summary = client.post(f"/api/conversations/{conv}/end", headers=admin_headers).json()
    assert summary["outcome"] == "resolved" and summary["primary_intent"] == "order_cancellation"
    assert "Cancelled order ORD003" in summary["actions_taken"][0]


def test_flow2_declined_confirmation_changes_nothing(client, admin_headers, start_conversation, say):
    conv = start_conversation("CUST002")  # identified caller
    r = say(conv, "Please cancel my order")
    assert _tools(r) == [("cancel_order", "confirmation_required")]  # the only cancellable order: ORD003
    r = say(conv, "No, don't cancel it")
    assert r["tool_calls"] == [] and "haven't made any changes" in r["content"]
    assert _order_status(client, admin_headers, "ORD003") == "confirmed"


def test_flow3_policy_question_is_grounded(start_conversation, say):
    conv = start_conversation()
    r = say(conv, "How long do refunds take after you receive a return?")
    assert r["tool_calls"] == []
    assert r["rag_sources"] and "5-7 business days" in r["content"]


def test_flow4_human_escalation_is_recorded(client, admin_headers, start_conversation, say):
    conv = start_conversation("CUST008")
    r = say(conv, "I was charged twice and I want to speak to a real person")
    assert r["status"] == "escalated" and _tools(r) == [("escalate_to_human", "escalated")]

    detail = client.get(f"/api/conversations/{conv}", headers=admin_headers).json()
    assert detail["conversation"]["status"] == "escalated"
    assert detail["escalation"]["customer_code"] == "CUST008" and detail["escalation"]["ticket_id"]

    escalations = client.get("/api/escalations", headers=admin_headers).json()
    assert any(e["conversation_id"] == conv for e in escalations)
    client.post(f"/api/conversations/{conv}/end", headers=admin_headers)
    overview = client.get("/api/analytics/overview", headers=admin_headers).json()
    assert overview["escalated_conversations"] == 1 and overview["escalation_rate"] == 100.0
    intents = client.get("/api/analytics/intents", headers=admin_headers).json()
    assert intents[0]["intent"] == "payment_issue"


def test_flow5_ambiguous_request_then_auto_escalation(client, admin_headers, start_conversation, say):
    conv = start_conversation()
    r = say(conv, "the blue thing doesn't do the stuff")
    assert r["tool_calls"] == [] and "?" in r["content"]
    say(conv, "you know, the thingy")
    r = say(conv, "blah blah")
    # 3 consecutive unresolved turns -> backend escalates (escalation_policy.md)
    assert r["status"] == "escalated" and r["tool_calls"][-1]["tool_name"] == "escalate_to_human"
    detail = client.get(f"/api/conversations/{conv}", headers=admin_headers).json()
    assert detail["escalation"]["reason"] == "repeated_failure"


def test_conversation_memory_resolves_it(start_conversation, say):
    conv = start_conversation("CUST001")
    say(conv, "My order hasn't arrived, it's ORD001")
    r = say(conv, "Can I cancel it?")
    assert _tools(r) == [("cancel_order", "not_eligible")]
    assert r["tool_calls"][0]["arguments"]["order_id"] == "ORD001"
    r = say(conv, "What about returning ORD002 instead? The lamp flickers.")
    assert _tools(r) == [("create_return_request", "confirmation_required")]
    assert r["active_order_id"] == "ORD002"


def test_messages_and_tool_executions_are_stored(client, admin_headers, start_conversation, say):
    conv = start_conversation()
    say(conv, "status of ORD005 please")
    detail = client.get(f"/api/conversations/{conv}", headers=admin_headers).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][0]["intent"] == "order_status"
    assert detail["messages"][1]["processing_time_ms"] > 0
    execution = detail["tool_executions"][0]
    assert execution["tool_name"] == "get_order_status" and execution["message_id"] == detail["messages"][1]["id"]


def test_ended_conversation_rejects_messages(client, admin_headers, start_conversation, say):
    conv = start_conversation()
    say(conv, "hello")
    end = client.post(f"/api/conversations/{conv}/end", headers=admin_headers).json()
    assert end["status"] == "completed" and end["summary"]
    r = client.post(f"/api/conversations/{conv}/messages", json={"content": "hi again"}, headers=admin_headers)
    assert r.status_code == 409


# ── LLM path guardrails ──

class ScriptedLLM:
    """Fake provider replaying scripted responses (each item: LLMResponse or Exception)."""
    name = "scripted:test"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def chat(self, messages, tools=None, **kwargs):
        self.calls.append({"messages": messages, "tools": tools})
        item = self.script.pop(0) if self.script else LLMResponse(content="Okay.")
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def llm_only(monkeypatch):
    """Send every turn through the LLM path (disable the deterministic policy route)."""
    monkeypatch.setattr(agent_service, "POLICY_ROUTE_INTENTS", set())
    monkeypatch.setattr(agent_service.CustomerSupportAgent, "_use_policy_route",
                        staticmethod(lambda *a, **k: False))


def _call(name, **args):
    return LLMResponse(tool_calls=[{"id": f"c_{name}", "name": name, "arguments": args}])


def test_llm_cannot_cancel_without_customer_confirmation(llm_only, client, admin_headers, start_conversation, say):
    llm = ScriptedLLM([_call("cancel_order", order_id="ORD003"),
                       LLMResponse(content="I have cancelled your order.")])  # model tries to claim success
    llm_service.set_llm_provider(llm)
    conv = start_conversation("CUST002")
    r = say(conv, "Hmm, what should I do about my watch order ORD003")  # not routed to the policy
    assert _tools(r) == [("cancel_order", "confirmation_required")]
    assert r["status"] == "awaiting_confirmation"
    assert _order_status(client, admin_headers, "ORD003") == "confirmed"
    # The tool result handed back to the model says nothing changed
    tool_msg = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"][0]
    assert "confirmation_required" in tool_msg["content"]


def test_llm_cannot_touch_another_customers_order(llm_only, client, admin_headers, start_conversation, say):
    llm_service.set_llm_provider(ScriptedLLM([_call("get_order", order_id="ORD009"),
                                              LLMResponse(content="I can't access that order.")]))
    conv = start_conversation("CUST002")
    r = say(conv, "Tell me something about ORD009 and its details")
    assert _tools(r) == [("get_order", "not_owner")]


def test_llm_receives_context_and_static_prompt(llm_only, start_conversation, say):
    llm = ScriptedLLM([LLMResponse(content="We ship to select countries.")])
    llm_service.set_llm_provider(llm)
    conv = start_conversation("CUST002")
    say(conv, "Do you ship internationally?")
    messages = llm.calls[0]["messages"]
    assert messages[0]["role"] == "system" and "Knowledge" in messages[0]["content"]
    # knowledge-only prompt: retrieved passages, but no customer/order data the model could mix in
    assert "International" in messages[-1]["content"] and "CUST002" not in messages[-1]["content"]
    assert llm.calls[0]["tools"] is None  # knowledge turn: answered from RAG, no tools offered


def test_llm_failure_falls_back_to_offline_policy(llm_only, start_conversation, say):
    llm_service.set_llm_provider(ScriptedLLM([LLMError("connection refused")]))
    conv = start_conversation()
    r = say(conv, "Hmm, could you check something on ORD001 for me")
    assert r["degraded"] is True and r["provider"] == "offline"
    assert _tools(r) == [("get_order_status", "shipped")]


def test_outcome_unresolved_when_request_left_open(client, admin_headers, start_conversation, say):
    conv = start_conversation()
    say(conv, "I want to cancel my order")  # agent asks for the order number; caller hangs up
    end = client.post(f"/api/conversations/{conv}/end", headers=admin_headers).json()
    assert end["outcome"] == "unresolved"

    conv = start_conversation("CUST001")
    say(conv, "Can I cancel ORD001?")  # definitively answered: shipped orders can't be cancelled
    end = client.post(f"/api/conversations/{conv}/end", headers=admin_headers).json()
    assert end["outcome"] == "resolved"


# ── Grounded knowledge answers ──

def test_knowledge_answer_with_invented_facts_is_replaced(llm_only, start_conversation, say):
    hallucination = ("Our return policy allows returns within 30 days. Since ORD001 was delivered on October 8, "
                     "you have until October 8 of next year to return it.")
    llm = ScriptedLLM([LLMResponse(content=hallucination)])
    llm_service.set_llm_provider(llm)
    conv = start_conversation("CUST001")
    r = say(conv, "What is your return policy?")
    assert "October" not in r["content"] and r["content"].startswith("According to our")
    assert r["rag_sources"]
    sent = llm.calls[0]["messages"][-1]["content"]
    assert "ORD001" not in sent and "James" not in sent  # no customer/order data in a knowledge-only prompt
    assert llm.calls[0]["tools"] is None


def test_knowledge_answer_is_capped_for_voice(llm_only, start_conversation, say):
    rambling = ("Refunds are processed within 5-7 business days. You get an email. Allow 3-5 business days. "
                "Contact us anytime. We are here to help.")
    llm_service.set_llm_provider(ScriptedLLM([LLMResponse(content=rambling)]))
    conv = start_conversation()
    r = say(conv, "How long do refunds take after a return?")
    assert r["content"] == "Refunds are processed within 5-7 business days. You get an email."


def test_policy_question_without_knowledge_does_not_guess(llm_only, start_conversation, say):
    llm = ScriptedLLM([LLMResponse(content="Yes, we offer lifetime price matching!")])
    llm_service.set_llm_provider(llm)
    conv = start_conversation()
    r = say(conv, "What is your policy on price matching for competitor stores?")
    assert "don't have information" in r["content"] and not llm.calls and r["rag_sources"] == []


# ── After escalation ──

def test_turns_after_escalation_stay_coherent(client, admin_headers, start_conversation, say):
    conv = start_conversation("CUST002")
    first = say(conv, "I want to speak to a human")
    ticket = first["tool_calls"][0]["result"]["ticket_id"]
    r = say(conv, "This is ridiculous, I'm so frustrated")
    assert r["status"] == "escalated" and r["tool_calls"] == []
    assert ticket in r["content"] and "already with our human support team" in r["content"]
    r = say(conv, "Get me a person!")  # no duplicate escalation
    assert r["tool_calls"] == [] and ticket in r["content"]
    r = say(conv, "What is the status of ORD003?")  # still helpful while waiting
    assert r["tool_calls"][0]["tool_name"] == "get_order_status"
    escalations = client.get("/api/escalations", headers=admin_headers).json()
    assert len([e for e in escalations if e["conversation_id"] == conv]) == 1


def test_empty_llm_reply_after_escalation_uses_handoff_message(llm_only, start_conversation, say):
    llm_service.set_llm_provider(ScriptedLLM([_call("escalate_to_human", reason="customer_request"),
                                              LLMResponse(content="Transferring you now."),
                                              LLMResponse(content="")]))
    conv = start_conversation("CUST002")
    say(conv, "hmm I think someone else should look at this")
    r = say(conv, "can you look at my account details")  # tool-capable turn; the model returns nothing
    assert r["content"] != "Sorry, could you say that again?" and "human support team" in r["content"]


def test_nonexistent_order_is_not_stored_in_memory(start_conversation, say):
    conv = start_conversation("CUST001")
    say(conv, "My order hasn't arrived, it's ORD001")
    r = say(conv, "Sorry, the order number is ORD123")
    assert r["tool_calls"][0]["result"]["error_code"] == "order_not_found"
    assert r["active_order_id"] == "ORD001"  # the invalid ID never replaced the verified one
    r = say(conv, "Can I cancel it?")
    assert r["tool_calls"][0]["arguments"]["order_id"] == "ORD001"


def test_unverified_caller_bad_order_id_leaves_no_active_order(start_conversation, say):
    conv = start_conversation()
    r = say(conv, "What's the status of ORD999?")
    assert r["tool_calls"][0]["result"]["error_code"] == "order_not_found" and r["active_order_id"] is None


# ── Summaries cannot embellish ──

def test_summary_rejects_embellished_llm_issue(client, admin_headers, start_conversation, say):
    embellished = ('{"issue": "Customer was promised a $50 credit and delivery by October 12 for ORD001.", '
                   '"sentiment": "positive"}')
    llm = ScriptedLLM([LLMResponse(content=embellished)])
    llm_service.set_llm_provider(llm)
    conv = start_conversation()
    say(conv, "status of ORD005 please")  # confident status turn: policy route, no LLM call
    end = client.post(f"/api/conversations/{conv}/end", headers=admin_headers).json()
    assert len(llm.calls) == 1  # only the summary used the LLM
    assert end["issue"] == "status of ORD005 please" and "October" not in end["summary"]
    assert end["outcome"] == "resolved" and end["sentiment"] == "positive"


def test_summary_accepts_faithful_llm_issue(client, admin_headers, start_conversation, say):
    llm_service.set_llm_provider(ScriptedLLM([LLMResponse(
        content='{"issue": "The customer asked for the status of order ORD005.", "sentiment": "neutral"}')]))
    conv = start_conversation()
    say(conv, "status of ORD005 please")
    end = client.post(f"/api/conversations/{conv}/end", headers=admin_headers).json()
    assert end["issue"] == "The customer asked for the status of order ORD005."
    assert end["summary"].startswith("Order status: The customer asked for the status of order ORD005.")
    assert end["summary"].endswith("Outcome: resolved.")
