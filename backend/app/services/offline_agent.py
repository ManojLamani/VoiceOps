"""Offline agent policy: used when no LLM is configured, or as a fallback when the LLM fails.

It routes on the semantic intent + extracted entities + conversation state, calls the same
validated backend tools as the LLM path, and phrases replies only from tool results or
retrieved knowledge-base text, so it never claims an action or policy that didn't come
from the backend.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional

from app.services.nlu import sentiment_of
from app.services.text_utils import extract_order_id, extract_customer_id, extract_email, extract_phone, \
    extract_ticket_id

CLARIFY = ("Sorry, I didn't quite catch that. I can help with order status, cancellations, returns, refunds, "
           "deliveries, payments, your account, or product questions. What do you need?")
ASK_ORDER = "Sure. What's the order number? It starts with O R D, for example ORD003."
ASK_VERIFY = ("Before I can do that, I need to verify your account. What's the email address, phone number, "
              "or customer ID on the account?")


@dataclass
class Plan:
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    reply: Optional[str] = None
    failed: bool = False
    goal: Optional[Dict[str, Any]] = None  # remembered request to resume after verification


def _call(name: str, **args) -> Dict[str, Any]:
    return {"id": f"offline_{name}", "name": name, "arguments": {k: v for k, v in args.items() if v}}


def spoken_date(value: Optional[str]) -> str:
    """'2026-10-07' -> 'October 7' (adds the year only when it isn't the current year)."""
    if not value:
        return ""
    try:
        d = datetime.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    text = f"{d:%B} {d.day}"
    return text if d.year == datetime.utcnow().year else f"{text}, {d.year}"


def _first_name(name: str) -> str:
    return (name or "").split(" ")[0]


class OfflinePolicy:
    def plan(self, text: str, intent: str, state: Dict[str, Any], customer_orders: List[Dict],
             rag_results: List[Dict], rag) -> Plan:
        order_id = extract_order_id(text) or None
        customer_id = extract_customer_id(text)
        email, phone = extract_email(text), extract_phone(text)
        verified = bool(state.get("customer_uuid"))
        active = order_id or state.get("active_order_id")

        # Identity details offered -> verify first; resume the remembered goal afterwards.
        if (customer_id or email or phone) and not verified:
            return Plan([_call("get_customer", customer_id=customer_id, email=email, phone=phone)])

        goal = state.get("goal") or {}
        if goal.get("intent") in ("order_cancellation", "return_request") and order_id and \
                intent not in ("order_cancellation", "return_request", "escalation"):
            intent = goal["intent"]

        if intent == "escalation":
            return Plan([_call("escalate_to_human", reason="customer_request",
                               details=f"Customer asked for a human agent: \"{text}\"")])
        if intent == "complaint" or sentiment_of(text) == "negative":
            if sentiment_of(text) == "negative" and state.get("negative_turns", 0) >= 1:
                return Plan([_call("escalate_to_human", reason="customer_dissatisfied", details=text)])
            if verified:
                return Plan([_call("create_support_ticket", subject="Customer complaint", description=text,
                                   category="complaint", priority="high", order_id=active)])
            return Plan(reply="I'm sorry about that. " + ASK_VERIFY,
                        goal={"intent": "complaint", "text": text})
        if intent == "greeting":
            return Plan(reply="Hi, thanks for calling VoiceOps support. How can I help you today?")
        if intent == "goodbye":
            return Plan(reply="You're welcome. Thanks for contacting VoiceOps, have a great day!")

        if intent in ("order_status", "delivery_issue"):
            open_statuses = {"pending", "confirmed", "processing", "shipped", "in_transit"}
            target = active or self._only(customer_orders, open_statuses) or self._only(customer_orders, None)
            if not target:
                return Plan(reply=self._ask_which(customer_orders, open_statuses, "check on"), goal={"intent": intent})
            tool = "get_delivery_estimate" if intent == "delivery_issue" else "get_order_status"
            return Plan([_call(tool, order_id=target)])

        if intent == "order_cancellation":
            target = active or self._only(customer_orders, {"pending", "confirmed"})
            if not target:
                return Plan(reply=self._ask_which(customer_orders, {"pending", "confirmed"}, "cancel"),
                            goal={"intent": intent})
            return Plan([_call("cancel_order", order_id=target, reason=text)],
                        goal={"intent": intent, "order_id": target})

        if intent == "return_request":
            target = active or self._only(customer_orders, {"delivered"})
            if not target:
                return Plan(reply=self._ask_which(customer_orders, {"delivered"}, "return"), goal={"intent": intent})
            return Plan([_call("create_return_request", order_id=target, reason=text)],
                        goal={"intent": intent, "order_id": target})

        if intent == "payment_issue" and any(w in text.lower() for w in ("charged", "charge", "declined", "billing")):
            if verified:
                return Plan([_call("create_support_ticket", subject="Payment issue", description=text,
                                   category="payment", priority="high", order_id=active)])
            return Plan(reply=ASK_VERIFY, goal={"intent": intent, "text": text})

        ticket_id = extract_ticket_id(text)
        if ticket_id:
            return Plan([_call("get_ticket", ticket_id=ticket_id)])

        if intent == "refund_request" and active:
            return Plan([_call("get_order", order_id=active)])

        if order_id:
            return Plan([_call("get_order", order_id=order_id)])

        # Knowledge questions: answer only from retrieved knowledge.
        if rag_results:
            sentences = rag.best_sentences(text, rag_results)
            if sentences:
                title = rag_results[0]["document_title"]
                return Plan(reply=f"According to our {title}: " + " ".join(
                    s if s.endswith(".") else s + "." for s in sentences))
        if intent in ("policy_question", "product_information", "refund_request", "payment_issue",
                      "account_issue"):
            return Plan(reply="I don't have information about that in our knowledge base. "
                              "Would you like me to connect you with a human agent?", failed=True)
        return Plan(reply=CLARIFY, failed=True)

    def resume_goal(self, goal: Dict[str, Any], customer_orders: List[Dict]) -> Optional[Dict[str, Any]]:
        """After successful verification, continue what the customer originally asked for."""
        intent, target = goal.get("intent"), goal.get("order_id")
        if intent == "order_cancellation":
            target = target or self._only(customer_orders, {"pending", "confirmed"})
            return _call("cancel_order", order_id=target) if target else None
        if intent == "return_request":
            target = target or self._only(customer_orders, {"delivered"})
            return _call("create_return_request", order_id=target, reason=goal.get("text")) if target else None
        if intent == "complaint":
            return _call("create_support_ticket", subject="Customer complaint", description=goal.get("text", ""),
                         category="complaint", priority="high")
        if intent == "payment_issue":
            return _call("create_support_ticket", subject="Payment issue", description=goal.get("text", ""),
                         category="payment", priority="high")
        return None

    @staticmethod
    def _ask_which(orders: List[Dict], statuses: set, verb: str) -> str:
        candidates = [o for o in orders if o["status"] in statuses] or orders
        if not candidates:
            return ASK_ORDER
        listing = ", ".join(f"{o['order_id']} with the {o['items']}" for o in candidates[:3])
        return f"Which order would you like to {verb}? I see {listing}."

    @staticmethod
    def _only(orders: List[Dict], statuses: Optional[set]) -> Optional[str]:
        matches = [o for o in orders if statuses is None or o["status"] in statuses]
        return matches[0]["order_id"] if len(matches) == 1 else None

    # ── Phrasing from tool results ──

    def compose(self, results: List[Dict[str, Any]]) -> str:
        parts = []
        for i, r in enumerate(results):
            if r["tool_name"] == "get_customer" and r["success"] and len(results) > i + 1:
                parts.append(f"Thanks {_first_name(r['result']['name'])}, you're verified.")
                continue
            text = self._describe(r["tool_name"], r["result"])
            if text:
                parts.append(text)
        return " ".join(parts) or CLARIFY

    def _describe(self, tool: str, res: Dict[str, Any]) -> str:
        code = res.get("error_code")
        if res.get("status") == "confirmation_required":
            return f"{res['summary']} Would you like me to go ahead?"
        if code == "verification_required":
            return ASK_VERIFY
        if code == "order_not_found":
            return f"I couldn't find an order with the number {res.get('order_id')}. Could you double-check it?"
        if code == "customer_not_found":
            return "I couldn't find an account with those details. Could you try your email or customer ID again?"
        if code == "not_owner":
            return "That order isn't on the account I verified, so I can't share or change it."
        if code == "not_eligible":
            reason = re.sub(r"\d{4}-\d{2}-\d{2}", lambda m: spoken_date(m.group(0)), res["error"])
            return f"I'm sorry, I can't do that for order {res.get('order_id')}. {reason}"
        if "error" in res:
            return f"I wasn't able to complete that: {res['error']}"

        if tool == "get_customer":
            orders = res.get("recent_orders", [])
            listing = ", ".join(f"{o['order_id']} ({o['status'].replace('_', ' ')})" for o in orders[:3])
            return f"Thanks {_first_name(res['name'])}, your account is verified." + \
                (f" I can see your orders {listing}." if listing else "")
        if tool in ("get_order_status", "get_order"):
            status = res["status"].replace("_", " ")
            items = res["items"] if isinstance(res["items"], str) else \
                ", ".join(i.get("name", "") for i in res["items"])
            text = f"Order {res['order_id']} for {items} is {status}."
            if res["status"] == "return_requested":
                text = f"Order {res['order_id']} for {items} has a return in progress."
            if res.get("delivered_on"):
                text += f" It was delivered on {spoken_date(res['delivered_on'])}."
            elif res.get("estimated_delivery") and res["status"] not in ("cancelled",):
                text += f" It's expected to arrive on {spoken_date(res['estimated_delivery'])}."
            if res.get("tracking_number"):
                text += f" Tracking number {res['tracking_number']}."
            if tool == "get_order" and res.get("payment_status") in ("refund_pending", "refunded"):
                text += f" Payment status: {res['payment_status'].replace('_', ' ')}."
            return text
        if tool == "get_delivery_estimate":
            note = re.sub(r"\d{4}-\d{2}-\d{2}", lambda m: spoken_date(m.group(0)), res["delivery_note"])
            return f"Order {res['order_id']} is {res['status'].replace('_', ' ')}. {note}"
        if tool == "cancel_order":
            return (f"Done, order {res['order_id']} is cancelled. A refund of ${res['refund_amount']:.2f} is on "
                    f"its way: {res['refund_timeline']}")
        if tool == "create_return_request":
            return f"Your return {res['return_id']} is started. {res['next_steps']}"
        if tool == "update_customer":
            return "Your account details are updated."
        if tool == "create_support_ticket":
            return (f"I've opened ticket {res['ticket_id']} with {res['priority']} priority. "
                    f"Our team will follow up with you.")
        if tool == "get_ticket":
            return f"Ticket {res['ticket_id']}, {res['subject']}, is {res['status'].replace('_', ' ')}."
        if tool == "update_ticket":
            return f"I've updated ticket {res['ticket_id']}."
        if tool == "escalate_to_human":
            ticket = f" Your reference is ticket {res['ticket_id']}." if res.get("ticket_id") else ""
            eta = f" A specialist will respond {res['expected_response']}." if res.get("expected_response") else ""
            return "I'm transferring you to a human agent and passing along everything we discussed." + ticket + eta
        return ""
