"""Backend tools the agent can request.

The LLM only *requests* tool calls. Every call is validated here: arguments are checked
against the schema, identifiers are normalized, ownership is enforced against the verified
customer of the conversation, business rules come from app.services.policies, and
sensitive (state-changing) tools never run unless the agent service passes
``confirmed=True`` after the customer explicitly confirmed.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import (
    Customer, Order, OrderStatus, TicketStatus, TicketPriority, EscalationReason, AccountStatus,
)
from app.db.repositories import CustomerRepository, OrderRepository, TicketRepository, EscalationRepository
from app.services import policies
from app.services.text_utils import normalize_identifier, normalize_phone

logger = get_logger("agent_tools")

SENSITIVE_TOOLS = {"cancel_order", "create_return_request", "update_customer"}


def _fn(name: str, description: str, properties: Dict[str, Any], required: List[str] = None) -> Dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


_ORDER_ID = {"type": "string", "description": "Order ID, e.g. ORD003"}

TOOL_DEFINITIONS = [
    _fn("get_customer",
        "Look up and verify the customer's account using their customer ID, email or phone number. "
        "Returns the profile and recent orders. Required before any change to an order or account.",
        {"customer_id": {"type": "string", "description": "Customer ID, e.g. CUST002"},
         "email": {"type": "string", "description": "Email address on the account"},
         "phone": {"type": "string", "description": "Phone number on the account"}}),
    _fn("update_customer",
        "Update the verified customer's contact details (name, email, phone, address). Sensitive: the backend "
        "asks the customer to confirm before applying.",
        {"name": {"type": "string"}, "email": {"type": "string"},
         "phone": {"type": "string"}, "address": {"type": "string"}}),
    _fn("get_order", "Get full details of an order: items, amount, dates, status, eligibility.",
        {"order_id": _ORDER_ID}, ["order_id"]),
    _fn("get_order_status", "Get the current status, tracking number and delivery estimate of an order.",
        {"order_id": _ORDER_ID}, ["order_id"]),
    _fn("get_delivery_estimate", "Get the estimated or actual delivery date of an order.",
        {"order_id": _ORDER_ID}, ["order_id"]),
    _fn("cancel_order",
        "Request cancellation of an order. The backend checks eligibility and ownership, then asks the "
        "customer to confirm before cancelling. Never claim the order is cancelled until the result says so.",
        {"order_id": _ORDER_ID, "reason": {"type": "string", "description": "Customer's reason"}}, ["order_id"]),
    _fn("create_return_request",
        "Request a return for a delivered order inside the 30-day window. The backend checks eligibility and "
        "asks the customer to confirm before creating it.",
        {"order_id": _ORDER_ID, "reason": {"type": "string", "description": "Why the customer is returning it"}},
        ["order_id"]),
    _fn("create_support_ticket",
        "Create a support ticket for issues that need follow-up (payment problems, damaged items, complaints, "
        "warranty claims, delivery problems).",
        {"subject": {"type": "string"}, "description": {"type": "string"},
         "category": {"type": "string", "enum": ["delivery", "return", "refund", "payment", "warranty",
                                                  "account", "product", "complaint", "general"]},
         "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
         "order_id": {"type": "string", "description": "Related order ID, if any"}},
        ["subject", "description"]),
    _fn("get_ticket", "Get the status of an existing support ticket.",
        {"ticket_id": {"type": "string", "description": "Ticket ID, e.g. TKT0004"}}, ["ticket_id"]),
    _fn("update_ticket", "Add information to a ticket or change its status/priority.",
        {"ticket_id": {"type": "string"}, "note": {"type": "string", "description": "Information to add"},
         "status": {"type": "string", "enum": ["open", "in_progress", "waiting_customer", "resolved", "closed"]},
         "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]}},
        ["ticket_id"]),
    _fn("escalate_to_human",
        "Transfer the conversation to a human agent. Use when the customer asks for a person, is clearly "
        "dissatisfied, the issue is sensitive (fraud, security, legal), unsupported, or you cannot resolve it.",
        {"reason": {"type": "string", "enum": [r.value for r in EscalationReason]},
         "details": {"type": "string", "description": "Short description of the issue for the human agent"}},
        ["reason"]),
]

_SCHEMAS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOL_DEFINITIONS}

ESCALATION_PRIORITY = {
    EscalationReason.SENSITIVE_ISSUE: (TicketPriority.URGENT, "immediately"),
    EscalationReason.CUSTOMER_DISSATISFIED: (TicketPriority.HIGH, "within 1 hour"),
    EscalationReason.REPEATED_FAILURE: (TicketPriority.HIGH, "within 1 hour"),
    EscalationReason.POLICY_REQUIRED: (TicketPriority.MEDIUM, "within 4 hours"),
    EscalationReason.CUSTOMER_REQUEST: (TicketPriority.MEDIUM, "within 4 hours"),
    EscalationReason.LOW_CONFIDENCE: (TicketPriority.MEDIUM, "within 4 hours"),
    EscalationReason.OUTSIDE_SCOPE: (TicketPriority.LOW, "within 24 hours"),
}


@dataclass
class ToolContext:
    conversation_id: UUID
    customer: Optional[Customer] = None          # customer verified in this conversation
    confirmed: bool = False                      # True only when executing a customer-confirmed action
    escalation_summary: str = ""                 # transcript excerpt handed to humans on escalation
    tools_used: List[Dict] = field(default_factory=list)


def _val(enum_value):
    return enum_value.value if hasattr(enum_value, "value") else str(enum_value)


def _date(dt: Optional[datetime]) -> Optional[str]:
    return dt.date().isoformat() if dt else None


def _items(order: Order) -> str:
    return ", ".join(f"{p.get('quantity', 1)}x {p.get('name')}" for p in (order.products or []))


class ToolExecutor:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.customers = CustomerRepository(db)
        self.orders = OrderRepository(db)
        self.tickets = TicketRepository(db)
        self.escalations = EscalationRepository(db)

    # ── Entry point ──

    async def execute(self, tool_name: str, arguments: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
        start = time.perf_counter()
        handler = getattr(self, f"_tool_{tool_name}", None)
        schema = _SCHEMAS.get(tool_name)
        if not handler or not schema:
            result = {"error": f"Unknown tool '{tool_name}'.", "error_code": "unknown_tool"}
        else:
            args = {k: v for k, v in (arguments or {}).items()
                    if k in schema["properties"] and v not in (None, "")}
            missing = [r for r in schema.get("required", []) if r not in args]
            if missing:
                result = {"error": f"Missing required parameter(s): {', '.join(missing)}. Ask the customer.",
                          "error_code": "missing_parameters"}
            else:
                try:
                    result = await handler(ctx, **args)
                except Exception as e:  # database/unexpected failure -> structured error, never a crash
                    logger.error("tool_error", tool=tool_name, error=str(e))
                    result = {"error": "A system error occurred while running this action.",
                              "error_code": "system_error"}
        result["_execution_time_ms"] = round((time.perf_counter() - start) * 1000, 2)
        logger.info("tool_executed", tool=tool_name, ok="error" not in result,
                    elapsed_ms=result["_execution_time_ms"])
        return result

    # ── Helpers ──

    async def _find_order(self, ctx: ToolContext, order_id: str, require_verified: bool = False
                          ) -> Tuple[Optional[Order], Optional[Dict]]:
        oid = normalize_identifier(order_id, "ORD", 3)
        order = await self.orders.get_by_order_id(oid)
        if not order:
            return None, {"error": f"No order found with ID {oid}. Ask the customer to double-check it.",
                          "error_code": "order_not_found", "order_id": oid}
        if ctx.customer and order.customer_id != ctx.customer.id:
            return None, {"error": f"Order {oid} is not on the verified customer's account.",
                          "error_code": "not_owner", "order_id": oid}
        if require_verified and not ctx.customer:
            return None, {"error": "Identity verification is required before changing an order. Ask for the "
                                   "email, phone number or customer ID on the account, then call get_customer.",
                          "error_code": "verification_required", "order_id": oid, "_active_order_id": oid}
        return order, None

    def _confirmation(self, tool: str, args: Dict, summary: str, **extra) -> Dict:
        return {
            "status": "confirmation_required",
            "action": tool,
            "summary": summary,
            "instructions": "Nothing has been changed yet. Read the summary to the customer and ask them to "
                            "explicitly confirm (yes/no).",
            "_pending": {"tool": tool, "args": args, "summary": summary},
            **extra,
        }

    # ── Customer tools ──

    async def _tool_get_customer(self, ctx: ToolContext, customer_id: str = None, email: str = None,
                                 phone: str = None) -> Dict:
        customer = None
        if customer_id:
            customer = await self.customers.get_by_customer_id(normalize_identifier(customer_id, "CUST", 3))
        if not customer and email:
            customer = await self.customers.get_by_email(email.strip().lower())
        if not customer and phone:
            wanted = normalize_phone(phone)
            for c in await self.customers.get_all(limit=1000):
                if wanted and normalize_phone(c.phone or "") == wanted:
                    customer = c
                    break
        if not (customer_id or email or phone):
            return {"error": "Provide a customer ID, email or phone number.", "error_code": "missing_parameters"}
        if not customer:
            return {"error": "No account matches those details.", "error_code": "customer_not_found"}
        if ctx.customer and ctx.customer.id != customer.id:
            return {"error": "This conversation is already verified for a different account.",
                    "error_code": "not_owner"}
        orders = await self.orders.get_by_customer(customer.id)
        return {
            "verified": True,
            "customer_id": customer.customer_id,
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "account_status": _val(customer.account_status),
            "recent_orders": [
                {"order_id": o.order_id, "status": _val(o.status), "items": _items(o),
                 "total": round(o.total_amount, 2), "order_date": _date(o.order_date)}
                for o in orders[:5]
            ],
            "_bind_customer_id": str(customer.id),
        }

    async def _tool_update_customer(self, ctx: ToolContext, **fields) -> Dict:
        if not ctx.customer:
            return {"error": "Verify the customer's identity first (get_customer).",
                    "error_code": "verification_required"}
        if ctx.customer.account_status != AccountStatus.ACTIVE:
            return {"error": f"Account is {_val(ctx.customer.account_status)}; changes need a human agent.",
                    "error_code": "account_not_active"}
        changes = {k: v.strip() for k, v in fields.items() if k in ("name", "email", "phone", "address")}
        if not changes:
            return {"error": "No valid fields to update.", "error_code": "missing_parameters"}
        if "email" in changes:
            changes["email"] = changes["email"].lower()
            other = await self.customers.get_by_email(changes["email"])
            if other and other.id != ctx.customer.id:
                return {"error": "That email is already used by another account.", "error_code": "conflict"}
        summary = "Update " + ", ".join(f"{k} to '{v}'" for k, v in changes.items()) + \
                  f" on account {ctx.customer.customer_id}."
        if not ctx.confirmed:
            return self._confirmation("update_customer", changes, summary)
        await self.customers.update(ctx.customer, **changes)
        return {"status": "updated", "customer_id": ctx.customer.customer_id, "updated_fields": changes}

    # ── Order tools ──

    async def _tool_get_order(self, ctx: ToolContext, order_id: str) -> Dict:
        order, err = await self._find_order(ctx, order_id)
        if err:
            return err
        cancel = policies.cancellation_eligibility(order)
        ret = policies.return_eligibility(order)
        return {
            "order_id": order.order_id,
            "items": order.products,
            "total_amount": round(order.total_amount, 2),
            "order_date": _date(order.order_date),
            "status": _val(order.status),
            "estimated_delivery": _date(order.estimated_delivery),
            "delivered_on": _date(order.actual_delivery),
            "tracking_number": order.tracking_number,
            "payment_status": order.payment_status,
            "can_cancel": cancel.eligible, "cancellation_note": cancel.reason,
            "can_return": ret.eligible, "return_note": ret.reason,
            "_active_order_id": order.order_id,
        }

    async def _tool_get_order_status(self, ctx: ToolContext, order_id: str) -> Dict:
        order, err = await self._find_order(ctx, order_id)
        if err:
            return err
        return {
            "order_id": order.order_id,
            "status": _val(order.status),
            "items": _items(order),
            "tracking_number": order.tracking_number,
            "estimated_delivery": _date(order.estimated_delivery),
            "delivered_on": _date(order.actual_delivery),
            "_active_order_id": order.order_id,
        }

    async def _tool_get_delivery_estimate(self, ctx: ToolContext, order_id: str) -> Dict:
        order, err = await self._find_order(ctx, order_id)
        if err:
            return err
        status = policies.status_of(order)
        if status == OrderStatus.DELIVERED:
            note = f"Delivered on {_date(order.actual_delivery)}."
        elif status == OrderStatus.CANCELLED:
            note = "The order was cancelled and will not be delivered."
        elif order.estimated_delivery:
            days = (order.estimated_delivery.date() - datetime.utcnow().date()).days
            note = (f"Expected on {_date(order.estimated_delivery)} ({days} day(s) from today)."
                    if days >= 0 else f"Expected on {_date(order.estimated_delivery)}, which is {-days} day(s) late.")
        else:
            note = "No delivery estimate is available yet."
        return {"order_id": order.order_id, "status": status.value, "delivery_note": note,
                "estimated_delivery": _date(order.estimated_delivery), "delivered_on": _date(order.actual_delivery),
                "tracking_number": order.tracking_number, "_active_order_id": order.order_id}

    async def _tool_cancel_order(self, ctx: ToolContext, order_id: str, reason: str = None) -> Dict:
        order, err = await self._find_order(ctx, order_id, require_verified=True)
        if err:
            return err
        elig = policies.cancellation_eligibility(order)
        if not elig.eligible:
            return {"error": elig.reason, "error_code": "not_eligible", "order_id": order.order_id,
                    "status": _val(order.status), "_active_order_id": order.order_id}
        if not ctx.confirmed:
            summary = (f"Cancel order {order.order_id} ({_items(order)}) and refund "
                       f"${order.total_amount:.2f} to the original payment method.")
            return self._confirmation("cancel_order", {"order_id": order.order_id, "reason": reason or ""},
                                      summary, _active_order_id=order.order_id)
        await self.orders.update_status(
            order, OrderStatus.CANCELLED, cancellation_eligible=False, payment_status="refund_pending",
            refund_amount=order.total_amount,
            notes=f"Cancelled by customer via VoiceOps on {datetime.utcnow():%Y-%m-%d %H:%M} UTC. "
                  f"Reason: {reason or 'not given'}",
        )
        return {"status": "cancelled", "order_id": order.order_id, "refund_amount": round(order.total_amount, 2),
                "refund_timeline": "Refund processed within 1-2 business days; appears on the statement in 3-5 "
                                   "business days.", "_active_order_id": order.order_id}

    async def _tool_create_return_request(self, ctx: ToolContext, order_id: str, reason: str = None) -> Dict:
        order, err = await self._find_order(ctx, order_id, require_verified=True)
        if err:
            return err
        elig = policies.return_eligibility(order)
        if not elig.eligible:
            return {"error": elig.reason, "error_code": "not_eligible", "order_id": order.order_id,
                    "status": _val(order.status), "_active_order_id": order.order_id}
        if not ctx.confirmed:
            summary = (f"Start a return for order {order.order_id} ({_items(order)}). A prepaid label will be "
                       f"emailed; a ${policies.RETURN_SHIPPING_FEE} return shipping fee applies unless the item "
                       f"is defective.")
            return self._confirmation("create_return_request",
                                      {"order_id": order.order_id, "reason": reason or ""}, summary,
                                      _active_order_id=order.order_id)
        return_id = f"RET-{order.order_id}"
        await self.orders.update_status(
            order, OrderStatus.RETURN_REQUESTED, return_eligible=False,
            notes=f"Return {return_id} requested on {datetime.utcnow():%Y-%m-%d}. Reason: {reason or 'not given'}",
        )
        return {"status": "return_requested", "return_id": return_id, "order_id": order.order_id,
                "next_steps": "Prepaid return label emailed. Refund issued 5-7 business days after the item is "
                              "received and inspected.", "_active_order_id": order.order_id}

    # ── Ticket tools ──

    async def _tool_create_support_ticket(self, ctx: ToolContext, subject: str, description: str,
                                          category: str = "general", priority: str = "medium",
                                          order_id: str = None) -> Dict:
        if not ctx.customer:
            return {"error": "Verify the customer first (get_customer) so the ticket is linked to their account.",
                    "error_code": "verification_required"}
        if order_id:
            _, err = await self._find_order(ctx, order_id)
            if err:
                return err
            description = f"[Order {normalize_identifier(order_id, 'ORD', 3)}] {description}"
        ticket = await self.tickets.create(
            ticket_id=await self.tickets.next_ticket_id(), customer_id=ctx.customer.id,
            conversation_id=ctx.conversation_id, subject=subject[:480], description=description,
            category=category, priority=priority,
        )
        return {"ticket_id": ticket.ticket_id, "status": "open", "priority": _val(ticket.priority),
                "category": category}

    async def _find_ticket(self, ctx: ToolContext, ticket_id: str):
        tid = normalize_identifier(ticket_id, "TKT", 4)
        ticket = await self.tickets.get_by_ticket_id(tid)
        if not ticket or (ctx.customer and ticket.customer_id != ctx.customer.id):
            return None, {"error": f"No ticket {tid} found on this account.", "error_code": "ticket_not_found"}
        if not ctx.customer:
            return None, {"error": "Verify the customer first to access tickets.",
                          "error_code": "verification_required"}
        return ticket, None

    async def _tool_get_ticket(self, ctx: ToolContext, ticket_id: str) -> Dict:
        ticket, err = await self._find_ticket(ctx, ticket_id)
        if err:
            return err
        return {"ticket_id": ticket.ticket_id, "subject": ticket.subject, "status": _val(ticket.status),
                "priority": _val(ticket.priority), "category": ticket.category,
                "created": _date(ticket.created_at), "resolution": ticket.resolution}

    async def _tool_update_ticket(self, ctx: ToolContext, ticket_id: str, note: str = None,
                                  status: str = None, priority: str = None) -> Dict:
        ticket, err = await self._find_ticket(ctx, ticket_id)
        if err:
            return err
        changes = {}
        if note:
            changes["description"] = f"{ticket.description}\n\n[{datetime.utcnow():%Y-%m-%d %H:%M} UTC] {note}"
        if status:
            changes["status"] = TicketStatus(status)
            if status in ("resolved", "closed"):
                changes["resolved_at"] = datetime.utcnow()
        if priority:
            changes["priority"] = TicketPriority(priority)
        if not changes:
            return {"error": "Nothing to update.", "error_code": "missing_parameters"}
        await self.tickets.update(ticket, **changes)
        return {"ticket_id": ticket.ticket_id, "status": _val(ticket.status), "priority": _val(ticket.priority),
                "updated": sorted(k for k in changes if k != "resolved_at")}

    # ── Escalation ──

    async def _tool_escalate_to_human(self, ctx: ToolContext, reason: str, details: str = None) -> Dict:
        existing = await self.escalations.get_by_conversation(ctx.conversation_id)
        if existing and existing.status == "resolved":
            # A human already handled it and handed back, and the customer needs one again: back to the queue.
            existing.status, existing.assigned_to, existing.resolved_at = "pending", None, None
            return {"status": "escalated", "reason": _val(existing.reason), "ticket_id": existing.ticket_id,
                    "expected_response": ESCALATION_PRIORITY[existing.reason][1], "reopened": True,
                    "_escalation": True}
        if existing:
            return {"status": "escalated", "already_escalated": True, "ticket_id": existing.ticket_id,
                    "reason": _val(existing.reason)}
        esc_reason = EscalationReason(reason) if reason in EscalationReason._value2member_map_ \
            else EscalationReason.CUSTOMER_REQUEST
        priority, eta = ESCALATION_PRIORITY[esc_reason]
        ticket_id = None
        if ctx.customer:
            ticket = await self.tickets.create(
                ticket_id=await self.tickets.next_ticket_id(), customer_id=ctx.customer.id,
                conversation_id=ctx.conversation_id,
                subject=f"Escalation: {esc_reason.value.replace('_', ' ')}",
                description=(details or "Escalated by the AI agent.") + "\n\nTranscript excerpt:\n" +
                            ctx.escalation_summary,
                category="escalation", priority=priority.value,
            )
            await self.tickets.update(ticket, status=TicketStatus.ESCALATED)
            ticket_id = ticket.ticket_id
        customer_summary = (f"{ctx.customer.name} ({ctx.customer.customer_id}), {ctx.customer.email}, "
                            f"account {_val(ctx.customer.account_status)}") if ctx.customer \
            else "Unverified caller"
        await self.escalations.create(
            conversation_id=ctx.conversation_id, reason=esc_reason, details=details,
            customer_summary=customer_summary, conversation_summary=ctx.escalation_summary,
            tools_used=ctx.tools_used, customer_id=ctx.customer.id if ctx.customer else None,
            ticket_id=ticket_id,
        )
        return {"status": "escalated", "reason": esc_reason.value, "priority": priority.value,
                "expected_response": eta, "ticket_id": ticket_id, "_escalation": True}
