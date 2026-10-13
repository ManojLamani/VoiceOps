"""Customer-support agent orchestrator.

Per user turn:
  intent (semantic) -> RAG retrieval -> [pending confirmation gate] -> LLM tool loop
  (or offline policy) -> validated tool execution -> grounded reply -> persistence.

Conversation memory lives in two places: the message history (sent to the LLM) and a
structured state in ``conversation.metadata`` (verified customer, active order, pending
action, unfinished goal, failure counters) that is rendered into the system prompt and
used by the backend to enforce confirmation and escalation rules.
"""
import json
import re
import time
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable, Awaitable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import MessageRole, ConversationStatus, Conversation, Customer
from app.db.repositories import (
    ConversationRepository, MessageRepository, ToolExecutionRepository, EscalationRepository,
    CustomerRepository, OrderRepository,
)
from app.services.llm_service import get_llm_provider, LLMError
from app.services.nlu import get_intent_classifier, sentiment_of, TOOL_INTENTS, KNOWLEDGE_INTENTS
from app.services.offline_agent import OfflinePolicy
from app.services.rag_service import get_rag_service
from app.services.text_utils import (
    classify_confirmation, extract_order_id, extract_customer_id, extract_email, extract_phone, normalize_spoken_ids,
)
from app.services.tool_service import ToolExecutor, ToolContext, TOOL_DEFINITIONS, SENSITIVE_TOOLS

logger = get_logger("agent")

EventCallback = Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]]

MAX_TOOL_ROUNDS = 4
HISTORY_MESSAGES = 12
FAILURES_BEFORE_ESCALATION = 3       # escalation_policy.md: 3+ unsuccessful interactions
PENDING_TTL_TURNS = 3
TRANSACTIONAL_INTENTS = {"order_status", "order_cancellation", "return_request", "escalation", "greeting",
                         "goodbye"}
GOAL_INTENTS = {"order_cancellation", "return_request"}
POLICY_ROUTE_INTENTS = {"order_status", "delivery_issue", "order_cancellation", "return_request", "escalation"}
POLICY_ROUTE_MIN_CONFIDENCE = 0.6
ESCALATED_HOLD_INTENTS = {"escalation", "complaint", "general_inquiry"}
KNOWLEDGE_MAX_TOKENS = 110
FAILURE_CODES = {"order_not_found", "customer_not_found", "ticket_not_found", "missing_parameters", "system_error",
                 "unknown_tool"}
NO_TOOL_INTENTS = {"policy_question", "product_information", "greeting", "goodbye"}
ACTION_TOOLS = {"cancel_order", "create_return_request", "update_customer", "create_support_ticket",
                "update_ticket", "escalate_to_human"}

CONFIRM_TOOL = {
    "type": "function",
    "function": {
        "name": "resolve_pending_confirmation",
        "description": "Call this when the customer's message answers the pending confirmation question: "
                       "confirmed=true if they agree, false if they decline.",
        "parameters": {"type": "object", "properties": {"confirmed": {"type": "boolean"}},
                       "required": ["confirmed"]},
    },
}

# Static instructions: identical on every call so the provider can cache the prompt prefix (with tool schemas).
SYSTEM_PROMPT = """You are Ava, the voice customer-support agent for VoiceOps Store, an online electronics retailer.

Rules:
- Replies are spoken aloud: one or two short sentences, plain conversational text. No markdown, lists, or emojis.
- Use tools for anything about a specific customer, order, or ticket. Never guess or invent order details.
- Policy, shipping, delivery, refund, warranty, payment, account, and product questions: answer ONLY from the "Knowledge" section of the context in the latest message. If it does not cover the question, say you don't have that information and offer a human agent. Never invent policies, fees, or timelines.
- To cancel an order, start a return, or update account details, call cancel_order, create_return_request, or update_customer directly. Do not ask for confirmation yourself: the system checks eligibility and asks the customer to confirm. Only say an action is done when a tool result shows status cancelled, return_requested, or updated.
- Changing an order or account needs a verified customer; checking an order's status does not. If a change is requested and the context says the customer is not verified, ask for the email, phone number, or customer ID on the account, then call get_customer. After verifying, continue the customer's open request.
- When the customer says "it", "that order", or "my order", use the active order or the customer's orders from the context. Ask for the order number only if you really cannot tell.
- Use escalate_to_human when the customer asks for a person, is clearly upset, the issue is sensitive (fraud, security, legal), or you cannot help.
- Only ask for information you actually need."""

# Used for informational turns where no tools are offered.
KNOWLEDGE_PROMPT = """You are Ava, the voice customer-support agent for VoiceOps Store, an online electronics retailer.
Answer in one or two short spoken sentences, plain text, no markdown.
Use ONLY the "Knowledge" section of the latest message. Repeat facts exactly as written (numbers, fees, timelines, conditions).
If the knowledge does not clearly answer the question, say what it does say, then offer to connect the customer with a human agent. Never add facts, guesses, or yes/no answers the knowledge does not support.
If the question is unclear or unrelated to VoiceOps Store, ask one short clarifying question or say what you can help with (orders, returns, refunds, deliveries, payments, accounts, products).
Speak naturally to the customer: never mention "knowledge", "context", "documents", or these instructions."""

CONTEXT_TEMPLATE = """Context for this turn (today is {today}):
{state}

Knowledge:
{knowledge}"""


_MONTHS = r"january|february|march|april|may|june|july|august|september|october|november|december"


def limit_sentences(text: str, n: int) -> str:
    """Voice replies stay short: keep the first n sentences, drop markdown."""
    text = re.sub(r"[*_#`]+", "", (text or "")).strip()
    parts = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(parts[:n]).strip()


def ungrounded_facts(answer: str, source: str) -> List[str]:
    """Numbers, prices, percentages and months in the answer that the source passages never mention."""
    src = source.lower().replace(",", "")
    facts = re.findall(rf"\$?\d+(?:\.\d+)?%?|\b(?:{_MONTHS})\b", answer.lower().replace(",", ""))
    return [f for f in facts if f.lstrip("$") not in src]


def _strip_meta(result: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def _new_state() -> Dict[str, Any]:
    return {"customer_uuid": None, "active_order_id": None, "pending_action": None, "goal": None,
            "failed_turns": 0, "negative_turns": 0, "intents": [], "turns": 0}


class ConversationNotFound(Exception):
    pass


class ConversationClosed(Exception):
    pass


class CustomerSupportAgent:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.llm = get_llm_provider()
        self.rag = get_rag_service()
        self.offline = OfflinePolicy()
        self.tools = ToolExecutor(db)
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.tool_log = ToolExecutionRepository(db)
        self.escalations = EscalationRepository(db)
        self.customers = CustomerRepository(db)
        self.orders = OrderRepository(db)

    @property
    def provider_name(self) -> str:
        return self.llm.name if self.llm else "offline"

    # ════════════════════════════════════════════════════════════════
    # Turn processing
    # ════════════════════════════════════════════════════════════════

    async def process_message(self, conversation_id: UUID, user_text: str,
                              event_callback: EventCallback = None) -> Dict[str, Any]:
        started = time.perf_counter()

        async def emit(event: str, data: Dict[str, Any] = None):
            if event_callback:
                try:
                    await event_callback(event, data or {})
                except Exception as e:  # a dropped socket must not break the turn
                    logger.warning("event_emit_failed", event_type=event, error=str(e))

        conversation = await self.conversations.get_by_id(conversation_id)
        if not conversation:
            raise ConversationNotFound(str(conversation_id))
        if conversation.ended_at is not None:
            raise ConversationClosed(str(conversation_id))

        state = {**_new_state(), **((conversation.metadata_ or {}).get("state") or {})}
        state["turns"] += 1
        customer = await self._customer_from_state(state, conversation)
        text = user_text.strip()

        # 1. Understand: semantic intent + entities (memory of the order being discussed)
        intent, confidence, all_intents = get_intent_classifier().classify_detailed(text)
        mentioned_order = extract_order_id(text)
        if intent in GOAL_INTENTS and confidence >= 0.6:
            state["goal"] = {"intent": intent, "order_id": mentioned_order or state.get("active_order_id")}
        elif state.get("goal") and mentioned_order and not state["goal"].get("order_id"):
            state["goal"]["order_id"] = mentioned_order
        await emit("INTENT_DETECTED", {"intent": intent, "confidence": confidence, "all": all_intents})

        history = await self.messages.get_by_conversation(conversation_id)
        user_msg = await self.messages.create(conversation_id=conversation_id, role=MessageRole.USER,
                                              content=text, intent=intent, confidence=confidence)

        turn = {"tool_results": [], "rag": [], "content": "", "degraded": False, "failed": False}
        ctx = ToolContext(conversation_id=conversation_id, customer=customer,
                          escalation_summary=self._transcript_excerpt(history, text),
                          tools_used=await self._tools_used(conversation_id))

        try:
            was_escalated = bool(state.get("escalated"))
            handled = await self._handle_pending(text, state, ctx, turn, emit)
            if not handled and was_escalated and (
                    intent in ESCALATED_HOLD_INTENTS or "escalation" in all_intents
                    or sentiment_of(text) == "negative"):
                # Already handed to a human: don't re-escalate or improvise; restate the hand-off.
                turn["content"] = self._escalation_hold_message(state)
                handled = True
            if not handled:
                # 2. Retrieve knowledge (grounding for policy/product questions)
                await emit("RETRIEVING_KNOWLEDGE", {})
                try:
                    turn["rag"] = self.rag.retrieve(text)
                except Exception as e:
                    logger.error("rag_failed", error=str(e))
                    turn["rag"] = []
                turn["rag_used"] = self._knowledge_relevant(intent, turn["rag"])
                if not turn["rag_used"]:
                    turn["rag"] = []
                await emit("KNOWLEDGE_RETRIEVED", {"sources": self._sources(turn["rag"])})

                # An explicit request for a person wins routing, even inside a multi-intent turn.
                route_intent = "escalation" if "escalation" in all_intents else intent
                customer_orders = await self._customer_orders(ctx.customer)
                if self.llm and not self._use_policy_route(text, route_intent, confidence, state):
                    try:
                        await self._run_llm(text, route_intent, history, state, ctx, customer_orders, turn, emit)
                    except LLMError as e:
                        logger.error("llm_failed_using_offline", error=str(e))
                        turn["degraded"] = True
                        turn["tool_results"] = []
                        await self._run_offline(text, route_intent, state, ctx, customer_orders, turn, emit)
                else:
                    await self._run_offline(text, route_intent, state, ctx, customer_orders, turn, emit)

            await self._apply_escalation_rules(text, intent, state, ctx, turn, emit)
            content = turn["content"].strip() or (
                self._escalation_hold_message(state) if state.get("escalated") else "Sorry, could you say that again?")
            status = "responding"
        except Exception as e:  # database or unexpected failure: graceful reply, error surfaced to caller
            logger.error("agent_turn_failed", conversation_id=str(conversation_id), error=str(e))
            await self.db.rollback()
            raise

        # Ensure a freshly prepared sensitive action is actually put to the customer.
        pending = state.get("pending_action")
        if pending and pending.get("created_turn") == state["turns"] and "?" not in content:
            content = f"{content} {pending['summary']} Would you like me to go ahead?".strip()
        if state.get("escalated"):
            status = "escalated"
        elif pending:
            status = "awaiting_confirmation"

        # Track intents (tool-derived intent is more precise than the utterance intent)
        for r in turn["tool_results"]:
            if r["tool_name"] in TOOL_INTENTS and r["success"]:
                intent = TOOL_INTENTS[r["tool_name"]] if r["tool_name"] != "escalate_to_human" else intent
        recorded = [i for i in all_intents if i != intent] + [intent]
        recorded.sort(key=lambda i: i == "escalation")  # keep the underlying issue ahead of "escalation"
        for i in recorded:
            if i not in ("greeting", "goodbye", "general_inquiry") and (not state["intents"] or state["intents"][-1] != i):
                state["intents"] = (state["intents"] + [i])[-20:]

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        tool_calls_payload = [
            {"tool_name": r["tool_name"], "arguments": r["arguments"], "result": r["result"],
             "success": r["success"], "execution_time_ms": r["execution_time_ms"]}
            for r in turn["tool_results"]
        ]
        sources = self._sources(turn["rag"]) if turn.get("rag_used") else []
        assistant_msg = await self.messages.create(
            conversation_id=conversation_id, role=MessageRole.ASSISTANT, content=content, intent=intent,
            confidence=confidence, rag_sources=sources or None, tool_calls=tool_calls_payload or None,
            processing_time_ms=elapsed_ms,
        )
        for r in turn["tool_results"]:
            r["record"].message_id = assistant_msg.id

        await self._save_state(conversation, state, ctx)
        await self.db.flush()

        return {
            "message_id": str(assistant_msg.id),
            "user_message_id": str(user_msg.id),
            "content": content,
            "intent": intent,
            "confidence": confidence,
            "tool_calls": tool_calls_payload,
            "rag_sources": sources,
            "status": status,
            "pending_action": {"tool": pending["tool"], "summary": pending["summary"]} if pending else None,
            "customer": self._customer_payload(ctx.customer),
            "active_order_id": state.get("active_order_id"),
            "escalated": bool(state.get("escalated")),
            "provider": "offline" if (turn["degraded"] or not self.llm) else self.provider_name,
            "degraded": turn["degraded"],
            "processing_time_ms": elapsed_ms,
        }

    # ── Confirmation gate ──

    async def _handle_pending(self, text, state, ctx, turn, emit) -> bool:
        pending = state.get("pending_action")
        if not pending:
            return False
        if state["turns"] - pending.get("created_turn", state["turns"]) > PENDING_TTL_TURNS:
            state["pending_action"] = None
            return False
        decision = classify_confirmation(text)
        if decision is None and self.llm:
            decision = await self._llm_confirmation(text, pending)
        if decision is None:
            return False  # customer said something else; keep the pending action and carry on normally

        state["pending_action"] = None
        if decision is False:
            turn["content"] = "No problem, I haven't made any changes. Is there anything else I can help with?"
            return True

        # Confirmed: execute the stored, backend-validated action (it is re-validated on execution).
        ctx.confirmed = True
        result = await self._execute_tool(pending["tool"], pending["args"], state, ctx, turn, emit)
        ctx.confirmed = False
        turn["content"] = await self._phrase_results(
            [result], f"The customer confirmed: {pending['summary']}")
        return True

    async def _llm_confirmation(self, text: str, pending: Dict) -> Optional[bool]:
        try:
            resp = await self.llm.chat(
                [{"role": "system", "content": "You classify whether a customer's reply confirms a pending action. "
                                               "Call resolve_pending_confirmation only if the reply clearly "
                                               "accepts or declines. Otherwise reply with the word UNCLEAR."},
                 {"role": "assistant", "content": f"{pending['summary']} Would you like me to go ahead?"},
                 {"role": "user", "content": text}],
                tools=[CONFIRM_TOOL], max_tokens=60, temperature=0)
        except LLMError:
            return None
        for tc in resp.tool_calls:
            if tc["name"] == "resolve_pending_confirmation" and isinstance(tc["arguments"].get("confirmed"), bool):
                return tc["arguments"]["confirmed"]
        return None

    # ── LLM path ──

    async def _run_llm(self, text, intent, history, state, ctx, customer_orders, turn, emit):
        offer_tools = self._needs_tools(intent, state, ctx)
        if not offer_tools:
            await self._answer_from_knowledge(text, intent, history, turn, emit)
            return
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in history[-HISTORY_MESSAGES:]:
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            if role in ("user", "assistant") and m.content:
                messages.append({"role": role, "content": m.content})
        context = self._context_message(state, ctx, customer_orders, turn["rag"])
        messages.append({"role": "user", "content": f"{context}\n\nCustomer says: {normalize_spoken_ids(text)}"})

        await emit("GENERATING_RESPONSE", {})
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            tools = TOOL_DEFINITIONS if offer_tools and round_no < MAX_TOOL_ROUNDS else None
            resp = await self.llm.chat(messages, tools=tools)
            if not resp.tool_calls:
                turn["content"] = resp.content
                break
            messages.append({"role": "assistant", "content": resp.content or "", "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
                for tc in resp.tool_calls]})
            for tc in resp.tool_calls:
                result = await self._execute_tool(tc["name"], tc["arguments"], state, ctx, turn, emit)
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(_strip_meta(result["result"]), default=str)})
            await emit("GENERATING_RESPONSE", {})
        if not turn["content"]:
            turn["content"] = self.offline.compose(turn["tool_results"]) if turn["tool_results"] else ""
        turn["content"] = limit_sentences(turn["content"], 3)

        if self._tools_failed(turn["tool_results"]):
            turn["failed"] = True
        elif intent == "general_inquiry" and not turn["tool_results"] and not turn["rag"]:
            turn["failed"] = True  # nothing understood or grounded: counts toward repeated-failure escalation

    async def _answer_from_knowledge(self, text, intent, history, turn, emit):
        """Tool-free informational turn. Only the retrieved passages (and the previous exchange, for
        follow-ups) are given to the model; no customer/order data it could mix in. The answer is capped
        for voice and checked against the passages; anything ungrounded falls back to quoting them."""
        rag = turn["rag"]
        if not rag:
            # Nothing to ground on: never let the model improvise store facts.
            if intent in ("greeting", "goodbye"):
                turn["content"] = self.offline.plan(text, intent, {}, [], [], self.rag).reply
                return
            is_question = text.strip().endswith("?") or re.match(
                r"^\s*(what|how|do|does|did|can|could|is|are|will|would|when|where|why|which|who)\b", text, re.I)
            turn["content"] = (
                "I don't have information about that in our policies. "
                "Would you like me to connect you with a human agent?" if is_question else
                "Could you tell me a bit more about what you need? I can help with orders, returns, refunds, "
                "deliveries, payments, your account, or product questions.")
            turn["failed"] = True
            return
        messages = [{"role": "system", "content": KNOWLEDGE_PROMPT}]
        for m in history[-2:]:
            role = m.role.value if hasattr(m.role, "value") else str(m.role)
            if role in ("user", "assistant") and m.content:
                messages.append({"role": role, "content": m.content})
        knowledge = self.rag.format_context(rag)
        messages.append({"role": "user", "content": f"Knowledge:\n{knowledge}\n\nCustomer says: {text}"})
        await emit("GENERATING_RESPONSE", {})
        resp = await self.llm.chat(messages, temperature=0, max_tokens=KNOWLEDGE_MAX_TOKENS)
        answer = limit_sentences(resp.content, 2)
        source_text = " ".join(r["content"] for r in rag)
        if not answer or ungrounded_facts(answer, source_text):
            logger.info("knowledge_answer_replaced", reason="ungrounded" if answer else "empty", answer=answer[:120])
            sentences = self.rag.best_sentences(text, rag, max_sentences=2) if rag else []
            answer = (f"According to our {rag[0]['document_title']}: " + " ".join(
                x if x.endswith(".") else x + "." for x in sentences)) if sentences else \
                "Could you tell me a bit more about what you need help with?"
        turn["content"] = answer

    def _context_message(self, state, ctx, customer_orders, rag) -> str:
        lines = []
        if ctx.customer:
            lines.append(f"- Verified customer: {ctx.customer.name} ({ctx.customer.customer_id}), "
                         f"account {ctx.customer.account_status.value}")
            if customer_orders:
                lines.append("- Customer's orders: " + "; ".join(
                    f"{o['order_id']} {o['items']} ({o['status']})" for o in customer_orders[:5]))
        else:
            lines.append("- Customer: not verified yet")
        lines.append(f"- Active order: {state.get('active_order_id') or 'none'}")
        pending = state.get("pending_action")
        lines.append(f"- Waiting for customer confirmation of: {pending['summary']}" if pending
                     else "- Pending confirmation: none")
        goal = state.get("goal")
        if goal and goal.get("intent"):
            lines.append(f"- Customer's open request: {goal['intent'].replace('_', ' ')}"
                         + (f" for {goal['order_id']}" if goal.get("order_id") else ""))
        if state.get("escalated"):
            lines.append("- This conversation has been escalated to a human agent.")
        knowledge = self.rag.format_context(rag) if rag else "(none relevant to this message)"
        return CONTEXT_TEMPLATE.format(today=datetime.utcnow().strftime("%A, %B %d, %Y"),
                                       state="\n".join(lines), knowledge=knowledge)

    async def _phrase_results(self, results: List[Dict], note: str) -> str:
        """Turn tool results into a spoken reply (LLM when available, deterministic otherwise)."""
        fallback = self.offline.compose(results)
        if not self.llm:
            return fallback
        payload = json.dumps([{"tool": r["tool_name"], "result": _strip_meta(r["result"])} for r in results],
                             default=str)
        try:
            resp = await self.llm.chat([
                {"role": "system", "content": "You are Ava, a voice customer-support agent. In 1-2 short spoken "
                                              "sentences, tell the customer the outcome using ONLY these tool "
                                              "results. No markdown."},
                {"role": "user", "content": f"{note}\nTool results: {payload}"}], max_tokens=150)
            return resp.content or fallback
        except LLMError:
            return fallback

    # ── Offline path ──

    async def _run_offline(self, text, intent, state, ctx, customer_orders, turn, emit):
        plan = self.offline.plan(text, intent, state, customer_orders, turn["rag"], self.rag)
        if plan.goal:
            state["goal"] = {**plan.goal, "text": plan.goal.get("text") or text}
        turn["rag_used"] = bool(turn["rag"]) and plan.reply is not None and plan.reply.startswith("According")
        if not turn["rag_used"]:
            turn["rag"] = []
        if plan.reply:
            turn["content"], turn["failed"] = plan.reply, plan.failed
            return
        for call in plan.tool_calls:
            result = await self._execute_tool(call["name"], call["arguments"], state, ctx, turn, emit)
            # Resume the remembered request once the customer is verified.
            if call["name"] == "get_customer" and result["success"] and state.get("goal"):
                follow_up = self.offline.resume_goal(state["goal"], await self._customer_orders(ctx.customer))
                if follow_up:
                    await self._execute_tool(follow_up["name"], follow_up["arguments"], state, ctx, turn, emit)
        turn["content"] = self.offline.compose(turn["tool_results"])
        turn["failed"] = self._tools_failed(turn["tool_results"])

    # ── Tool execution (shared by both paths) ──

    async def _execute_tool(self, name, arguments, state, ctx, turn, emit) -> Dict[str, Any]:
        await emit("TOOL_CALL_STARTED", {"tool": name, "args": arguments})
        result = await self.tools.execute(name, arguments, ctx)
        exec_ms = result.pop("_execution_time_ms", 0)
        success = "error" not in result

        # Apply state changes requested by the tool result (never by the LLM directly).
        if result.get("_bind_customer_id"):
            ctx.customer = await self.customers.get_by_id(UUID(result["_bind_customer_id"]))
            state["customer_uuid"] = result["_bind_customer_id"]
        if result.get("_active_order_id"):
            state["active_order_id"] = result["_active_order_id"]
        if result.get("error_code") == "order_not_found":
            if state.get("goal") and state["goal"].get("order_id") == result.get("order_id"):
                state["goal"]["order_id"] = None
            if state.get("active_order_id") == result.get("order_id"):
                state["active_order_id"] = None
        if result.get("_pending"):
            state["pending_action"] = {**result["_pending"], "created_turn": state["turns"]}
            await emit("CONFIRMATION_REQUIRED", {"tool": name, "summary": result["_pending"]["summary"]})
        if result.get("error_code") == "verification_required" and name in SENSITIVE_TOOLS:
            state["goal"] = {"intent": TOOL_INTENTS.get(name, name), "order_id": result.get("order_id")}
        if name in ("cancel_order", "create_return_request") and (
                (success and result.get("status") != "confirmation_required")
                or result.get("error_code") == "not_eligible"):
            state["goal"] = None  # completed, or definitively answered as not possible
        if result.get("_escalation") or result.get("already_escalated"):
            state["escalated"] = True
            state["escalation_info"] = {"ticket_id": result.get("ticket_id"),
                                        "expected_response": result.get("expected_response")
                                        or (state.get("escalation_info") or {}).get("expected_response")}

        clean_args = {k: v for k, v in (arguments or {}).items()}
        clean_result = _strip_meta(result)
        record = await self.tool_log.create(
            conversation_id=ctx.conversation_id, tool_name=name, tool_args=clean_args, tool_result=clean_result,
            success=success, error_message=result.get("error"), execution_time_ms=exec_ms,
        )
        ctx.tools_used.append({"tool": name, "success": success})
        entry = {"tool_name": name, "arguments": clean_args, "result": clean_result, "success": success,
                 "execution_time_ms": exec_ms, "record": record}
        turn["tool_results"].append(entry)
        await emit("TOOL_CALL_COMPLETED", {"tool": name, "success": success, "result": clean_result,
                                           "execution_time_ms": exec_ms})
        if result.get("_escalation"):
            await emit("ESCALATED", {"reason": result.get("reason"), "ticket_id": result.get("ticket_id")})
        return entry

    # ── Escalation rules enforced by the backend ──

    async def _apply_escalation_rules(self, text, intent, state, ctx, turn, emit):
        if state.get("escalated"):
            return
        state["failed_turns"] = state["failed_turns"] + 1 if turn["failed"] else 0
        if not turn["failed"] and (turn.get("rag_used") or turn["tool_results"]):
            state["answered_turns"] = state.get("answered_turns", 0) + 1
        state["negative_turns"] = state["negative_turns"] + 1 if sentiment_of(text) == "negative" else 0

        reason, details = None, None
        if state["failed_turns"] >= FAILURES_BEFORE_ESCALATION:
            reason, details = "repeated_failure", f"{state['failed_turns']} consecutive unresolved turns."
        elif state["negative_turns"] >= 2:
            reason, details = "customer_dissatisfied", "Customer expressed frustration on consecutive turns."
        if not reason:
            return
        result = await self._execute_tool("escalate_to_human", {"reason": reason, "details": details},
                                          state, ctx, turn, emit)
        handoff = self.offline.compose([result])
        turn["content"] = f"{turn['content']} {handoff}".strip() if reason == "customer_dissatisfied" else \
            "I'm sorry I haven't been able to resolve this. " + handoff

    # ── End of conversation ──

    async def end_conversation(self, conversation_id: UUID, ended_at: Optional[datetime] = None) -> Dict[str, Any]:
        conversation = await self.conversations.get_by_id(conversation_id)
        if not conversation:
            raise ConversationNotFound(str(conversation_id))
        if conversation.ended_at is not None:
            return self._summary_payload(conversation)

        messages = await self.messages.get_by_conversation(conversation_id)
        executions = await self.tool_log.get_by_conversation(conversation_id)
        escalation = await self.escalations.get_by_conversation(conversation_id)
        state = {**_new_state(), **((conversation.metadata_ or {}).get("state") or {})}
        user_msgs = [m for m in messages if m.role == MessageRole.USER]
        customer = await self._customer_from_state(state, conversation)

        actions = [e for e in executions if e.success and e.tool_name in ACTION_TOOLS
                   and (e.tool_result or {}).get("status") != "confirmation_required"]
        if escalation:
            outcome, status = "escalated", ConversationStatus.ESCALATED
        elif not user_msgs:
            outcome, status = "abandoned", ConversationStatus.ABANDONED
        elif state.get("failed_turns", 0) > 0 or state.get("pending_action") or state.get("goal") \
                or not (actions or state.get("answered_turns")):
            # unanswered, abandoned mid-request, or left waiting for a confirmation
            outcome, status = "unresolved", ConversationStatus.COMPLETED
        else:
            outcome, status = "resolved", ConversationStatus.COMPLETED

        intents = state.get("intents") or []
        primary = next((i for i in intents if i != "escalation"), intents[0] if intents else "general_inquiry")
        sentiment = sentiment_of(" ".join(m.content for m in user_msgs))
        details = self._deterministic_summary(user_msgs, primary, outcome, actions, executions, escalation)
        if self.llm and user_msgs:
            llm_part = await self._llm_summary(messages, details)
            if llm_part.get("issue"):
                details["issue"] = llm_part["issue"]
                details["summary"] = self._summary_text(primary, llm_part["issue"], details["actions_taken"],
                                                        outcome)
            if llm_part.get("sentiment"):
                details["sentiment"] = llm_part["sentiment"]
        details.update({"intent": primary, "outcome": outcome,
                        "tools_used": sorted({e.tool_name for e in executions}),
                        "escalated": bool(escalation),
                        "escalation_reason": escalation.reason.value if escalation else None,
                        "customer": self._customer_payload(customer)})

        now = ended_at or datetime.utcnow()
        await self.conversations.update(
            conversation, status=status, primary_intent=primary, resolution=outcome,
            summary=details["summary"], summary_details=details, sentiment=details.get("sentiment") or sentiment,
            ended_at=now, duration_seconds=round((now - conversation.started_at).total_seconds(), 1),
        )
        if escalation:
            escalation.conversation_summary = details["summary"]
        await self.db.flush()
        return self._summary_payload(conversation)

    def _deterministic_summary(self, user_msgs, intent, outcome, actions, executions, escalation) -> Dict:
        issue = user_msgs[0].content[:200] if user_msgs else "No customer input."
        described = []
        for e in actions:
            r = e.tool_result or {}
            if e.tool_name == "cancel_order":
                described.append(f"Cancelled order {r.get('order_id')} (refund ${r.get('refund_amount')})")
            elif e.tool_name == "create_return_request":
                described.append(f"Created return {r.get('return_id')} for {r.get('order_id')}")
            elif e.tool_name == "create_support_ticket":
                described.append(f"Opened ticket {r.get('ticket_id')} ({r.get('category')})")
            elif e.tool_name == "update_customer":
                described.append("Updated customer details: " + ", ".join(r.get("updated_fields", {})))
            elif e.tool_name == "update_ticket":
                described.append(f"Updated ticket {r.get('ticket_id')}")
            elif e.tool_name == "escalate_to_human":
                described.append(f"Escalated to a human agent ({r.get('reason')})")
        lookups = sorted({e.tool_name for e in executions if e.success and e.tool_name not in ACTION_TOOLS})
        if escalation:
            follow_up = f"Human agent to contact the customer" + \
                        (f" via ticket {escalation.ticket_id}." if escalation.ticket_id else ".")
        elif any(e.tool_name == "cancel_order" for e in actions):
            follow_up = "Verify the cancellation refund is processed within 1-2 business days."
        elif any(e.tool_name == "create_return_request" for e in actions):
            follow_up = "Watch for the return shipment and issue the refund after inspection."
        elif outcome == "unresolved":
            follow_up = "Review the transcript; the customer's issue was not resolved."
        else:
            follow_up = "None required."
        resolution = {
            "resolved": "Customer's request was handled by the AI agent.",
            "escalated": "Handed off to a human agent.",
            "unresolved": "The issue was not fully resolved.",
            "abandoned": "Conversation ended without a customer request.",
        }[outcome]
        return {"issue": issue, "resolution": resolution, "actions_taken": described,
                "lookups": lookups, "follow_up": follow_up,
                "summary": self._summary_text(intent, issue, described, outcome)}

    @staticmethod
    def _summary_text(intent: str, issue: str, actions: List[str], outcome: str) -> str:
        summary = f"{intent.replace('_', ' ').capitalize()}: {issue}"
        if actions:
            summary += " Actions: " + "; ".join(actions) + "."
        return summary + f" Outcome: {outcome}."

    async def _llm_summary(self, messages, base: Dict) -> Dict:
        """The LLM only restates what the customer asked for, from the customer's own words, and labels
        sentiment. Outcome, actions and follow-up always come from database facts, so nothing can be
        embellished; an issue line that adds facts the customer never said is discarded."""
        customer_text = "\n".join(m.content for m in messages if m.role == MessageRole.USER)[-3000:]
        prompt = ("Below are a customer's messages to a support line. Reply with JSON: "
                  '{"issue": one short sentence stating only what the customer asked for or reported, '
                  '"sentiment": "positive" | "neutral" | "negative"}. Do not describe what the agent did, '
                  "outcomes, or anything the customer did not say.\n\nCustomer messages:\n" + customer_text)
        try:
            resp = await self.llm.chat([{"role": "user", "content": prompt}], max_tokens=120, temperature=0,
                                       json_mode=True)
            data = json.loads(resp.content)
        except Exception as e:
            logger.warning("llm_summary_failed", error=str(e))
            return {}
        out = {}
        issue = limit_sentences(str(data.get("issue") or ""), 1)
        source = customer_text.upper()
        ids_ok = all(i in source for i in re.findall(r"\b(?:ORD|CUST|TKT)\d+\b", issue.upper()))
        if issue and len(issue) <= 220 and ids_ok and not ungrounded_facts(issue, customer_text):
            out["issue"] = issue
        else:
            logger.info("llm_summary_issue_rejected", issue=issue[:120])
        if str(data.get("sentiment", "")).lower() in ("positive", "neutral", "negative"):
            out["sentiment"] = str(data["sentiment"]).lower()
        return out

    def _summary_payload(self, c: Conversation) -> Dict[str, Any]:
        details = c.summary_details or {}
        return {
            "conversation_id": str(c.id), "status": c.status.value, "outcome": c.resolution,
            "summary": c.summary, "primary_intent": c.primary_intent, "sentiment": c.sentiment,
            "duration_seconds": c.duration_seconds, **{k: details.get(k) for k in (
                "issue", "resolution", "actions_taken", "tools_used", "follow_up", "escalated",
                "escalation_reason")},
        }

    # ── Helpers ──

    @staticmethod
    def _escalation_hold_message(state: Dict) -> str:
        info = state.get("escalation_info") or {}
        ticket = f" under ticket {info['ticket_id']}" if info.get("ticket_id") else ""
        eta = f" A specialist will respond {info['expected_response']}." if info.get("expected_response") else \
            " A specialist will follow up with you."
        return (f"Your case is already with our human support team{ticket}.{eta} "
                "In the meantime, I can still check an order or answer a policy question.")

    @staticmethod
    def _tools_failed(results: List[Dict]) -> bool:
        """A turn fails when its tools found nothing or errored. Business-rule answers ("not eligible",
        "needs verification") are handled outcomes, not failures."""
        return bool(results) and all(not r["success"] and r["result"].get("error_code") in FAILURE_CODES
                                     for r in results)

    @staticmethod
    def _use_policy_route(text: str, intent: str, confidence: float, state: Dict) -> bool:
        """Confident transactional turns run through the deterministic policy + validated tools:
        lower latency and no dependence on the LLM choosing the right tool. Open-ended turns go to the LLM."""
        if not state.get("customer_uuid") and (extract_customer_id(text) or extract_email(text) or extract_phone(text)):
            return True
        if state.get("goal") and extract_order_id(text) and confidence < 0.6 and \
                intent in ("order_status", "general_inquiry"):
            return True
        return intent in POLICY_ROUTE_INTENTS and confidence >= POLICY_ROUTE_MIN_CONFIDENCE

    @staticmethod
    def _needs_tools(intent: str, state: Dict, ctx: ToolContext) -> bool:
        """Pure knowledge / small-talk turns are answered from RAG without tools (faster, and small models
        otherwise call tools needlessly)."""
        if intent in NO_TOOL_INTENTS:
            return False
        if intent in ("general_inquiry", "refund_request"):
            return bool(state.get("active_order_id") or ctx.customer)
        return True

    @staticmethod
    def _knowledge_relevant(intent: str, results: List[Dict]) -> bool:
        """Ground on the knowledge base for informational turns; transactional turns use tools instead."""
        if not results:
            return False
        if intent in KNOWLEDGE_INTENTS:
            return True
        return intent not in TRANSACTIONAL_INTENTS and results[0]["score"] >= 0.55

    async def _customer_from_state(self, state, conversation) -> Optional[Customer]:
        uuid = state.get("customer_uuid") or (str(conversation.customer_id) if conversation.customer_id else None)
        if not uuid:
            return None
        state["customer_uuid"] = uuid
        return await self.customers.get_by_id(UUID(uuid))

    async def _customer_orders(self, customer: Optional[Customer]) -> List[Dict]:
        if not customer:
            return []
        return [{"order_id": o.order_id, "status": o.status.value,
                 "items": ", ".join(p.get("name", "") for p in o.products or [])}
                for o in await self.orders.get_by_customer(customer.id)]

    async def _tools_used(self, conversation_id) -> List[Dict]:
        return [{"tool": e.tool_name, "success": e.success}
                for e in await self.tool_log.get_by_conversation(conversation_id)]

    @staticmethod
    def _transcript_excerpt(history, latest: str) -> str:
        lines = [f"{'Customer' if m.role == MessageRole.USER else 'Agent'}: {m.content}"
                 for m in history[-8:] if m.role in (MessageRole.USER, MessageRole.ASSISTANT)]
        return "\n".join(lines + [f"Customer: {latest}"])

    @staticmethod
    def _sources(results: List[Dict]) -> List[Dict]:
        return [{"document_title": r["document_title"], "section": r["section"], "chunk_id": r["chunk_id"],
                 "score": r["score"], "excerpt": r["content"].split("\n", 1)[-1][:240]} for r in results]

    @staticmethod
    def _customer_payload(c: Optional[Customer]) -> Optional[Dict]:
        return {"customer_id": c.customer_id, "name": c.name} if c else None

    async def _save_state(self, conversation: Conversation, state: Dict, ctx: ToolContext):
        metadata = dict(conversation.metadata_ or {})
        metadata["state"] = state
        conversation.metadata_ = metadata  # reassign so SQLAlchemy detects the JSON change
        intents = state.get("intents") or []
        updates = {"primary_intent": next((i for i in intents if i != "escalation"), intents[0] if intents else None)}
        if ctx.customer:
            updates["customer_id"] = ctx.customer.id
        if state.get("escalated"):
            updates["status"] = ConversationStatus.ESCALATED
        await self.conversations.update(conversation, **updates)
