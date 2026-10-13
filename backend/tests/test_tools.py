"""Backend tool validation: ownership, eligibility, confirmation gate, tickets, escalation."""
import uuid

import pytest

from app.db.database import async_session_factory
from app.db.models import Conversation, OrderStatus, Escalation, Ticket, TicketStatus
from app.db.repositories import CustomerRepository, OrderRepository, UserRepository
from app.services.tool_service import ToolExecutor, ToolContext


async def _tool(name, args, customer_code=None, confirmed=False):
    """Execute one tool in its own transaction; returns (result, order snapshot helper)."""
    async with async_session_factory() as s:
        admin = await UserRepository(s).get_by_email("admin@voiceops.ai")
        conv = Conversation(id=uuid.uuid4(), user_id=admin.id)
        s.add(conv)
        await s.flush()
        customer = await CustomerRepository(s).get_by_customer_id(customer_code) if customer_code else None
        ctx = ToolContext(conversation_id=conv.id, customer=customer, confirmed=confirmed,
                          escalation_summary="Customer: help")
        result = await ToolExecutor(s).execute(name, args, ctx)
        await s.commit()
        return result


async def _order(order_id):
    async with async_session_factory() as s:
        return await OrderRepository(s).get_by_order_id(order_id)


def test_get_customer_verifies_by_id_email_or_phone(run):
    assert run(_tool, "get_customer", {"customer_id": "cust 2"})["customer_id"] == "CUST002"
    by_email = run(_tool, "get_customer", {"email": "Emily.Chen@email.com"})
    assert by_email["verified"] and by_email["_bind_customer_id"]
    assert {o["order_id"] for o in by_email["recent_orders"]} == {"ORD003", "ORD004"}
    assert run(_tool, "get_customer", {"phone": "(555) 010-2000"})["error_code"] == "customer_not_found"
    assert run(_tool, "get_customer", {"phone": "+1 555 0102"})["customer_id"] == "CUST002"


def test_get_customer_cannot_switch_verified_account(run):
    assert run(_tool, "get_customer", {"customer_id": "CUST001"}, "CUST002")["error_code"] == "not_owner"


def test_order_lookup_and_id_normalization(run):
    r = run(_tool, "get_order_status", {"order_id": "ord-1"})
    assert r["order_id"] == "ORD001" and r["status"] == "shipped" and r["tracking_number"] == "TRK-2024-001"
    assert run(_tool, "get_order", {"order_id": "ORD999"})["error_code"] == "order_not_found"


def test_order_ownership_enforced(run):
    r = run(_tool, "get_order", {"order_id": "ORD003"}, "CUST001")
    assert r["error_code"] == "not_owner"


def test_cancel_requires_verification(run):
    r = run(_tool, "cancel_order", {"order_id": "ORD003"})
    assert r["error_code"] == "verification_required"
    assert run(_order, "ORD003").status == OrderStatus.CONFIRMED


def test_cancel_requires_confirmation_then_executes(run):
    r = run(_tool, "cancel_order", {"order_id": "ORD003", "reason": "changed mind"}, "CUST002")
    assert r["status"] == "confirmation_required" and "$199.99" in r["summary"]
    assert run(_order, "ORD003").status == OrderStatus.CONFIRMED  # nothing changed yet

    done = run(_tool, "cancel_order", {"order_id": "ORD003"}, "CUST002", confirmed=True)
    assert done["status"] == "cancelled" and done["refund_amount"] == 199.99
    order = run(_order, "ORD003")
    assert order.status == OrderStatus.CANCELLED and order.payment_status == "refund_pending"


@pytest.mark.parametrize("order_id,customer", [("ORD001", "CUST001"), ("ORD006", "CUST004"),
                                               ("ORD008", "CUST006"), ("ORD010", "CUST001")])
def test_cancel_rejects_ineligible_statuses(run, order_id, customer):
    # ORD001 is flagged cancellation_eligible in the data but has shipped: status rules win.
    r = run(_tool, "cancel_order", {"order_id": order_id}, customer, confirmed=True)
    assert r["error_code"] == "not_eligible"
    assert run(_order, order_id).status != OrderStatus.CANCELLED or order_id == "ORD010"


def test_return_rules(run):
    ok = run(_tool, "create_return_request", {"order_id": "ORD002", "reason": "flickers"}, "CUST001")
    assert ok["status"] == "confirmation_required"
    done = run(_tool, "create_return_request", {"order_id": "ORD002", "reason": "flickers"}, "CUST001",
               confirmed=True)
    assert done["status"] == "return_requested" and done["return_id"] == "RET-ORD002"
    assert run(_order, "ORD002").status == OrderStatus.RETURN_REQUESTED

    window = run(_tool, "create_return_request", {"order_id": "ORD007"}, "CUST005", confirmed=True)
    assert window["error_code"] == "not_eligible" and "30-day" in window["error"]
    final_sale = run(_tool, "create_return_request", {"order_id": "ORD011"}, "CUST003", confirmed=True)
    assert final_sale["error_code"] == "not_eligible"
    not_delivered = run(_tool, "create_return_request", {"order_id": "ORD001"}, "CUST001", confirmed=True)
    assert not_delivered["error_code"] == "not_eligible"


def test_update_customer_gated_and_validated(run):
    assert run(_tool, "update_customer", {"phone": "+1-555-9999"})["error_code"] == "verification_required"
    pending = run(_tool, "update_customer", {"phone": "+1-555-9999"}, "CUST002")
    assert pending["status"] == "confirmation_required"
    conflict = run(_tool, "update_customer", {"email": "james.wilson@email.com"}, "CUST002", confirmed=True)
    assert conflict["error_code"] == "conflict"
    suspended = run(_tool, "update_customer", {"phone": "1"}, "CUST007", confirmed=True)
    assert suspended["error_code"] == "account_not_active"


def test_ticket_lifecycle(run):
    assert run(_tool, "create_support_ticket", {"subject": "x", "description": "y"})["error_code"] == \
        "verification_required"
    t = run(_tool, "create_support_ticket", {"subject": "Double charge", "description": "Charged twice",
                                             "category": "payment", "priority": "high", "order_id": "ORD009"},
            "CUST008")
    assert t["ticket_id"] == "TKT0005" and t["priority"] == "high"
    assert run(_tool, "get_ticket", {"ticket_id": "TKT0005"}, "CUST008")["subject"] == "Double charge"
    assert run(_tool, "get_ticket", {"ticket_id": "TKT0005"}, "CUST001")["error_code"] == "ticket_not_found"
    upd = run(_tool, "update_ticket", {"ticket_id": "tkt 5", "note": "Bank statement attached",
                                       "status": "in_progress"}, "CUST008")
    assert upd["status"] == "in_progress"


def test_escalation_creates_records(run):
    r = run(_tool, "escalate_to_human", {"reason": "sensitive_issue", "details": "Possible fraud"}, "CUST002")
    assert r["status"] == "escalated" and r["priority"] == "urgent" and r["ticket_id"]

    async def check():
        from sqlalchemy import select
        async with async_session_factory() as s:
            esc = (await s.execute(select(Escalation))).scalars().all()
            ticket = (await s.execute(select(Ticket).where(Ticket.ticket_id == r["ticket_id"]))).scalar_one()
            return esc, ticket
    escalations, ticket = run(check)
    assert len(escalations) == 1 and escalations[0].ticket_id == r["ticket_id"]
    assert "Emily Chen" in escalations[0].customer_summary
    assert ticket.status == TicketStatus.ESCALATED


def test_schema_validation(run):
    assert run(_tool, "get_order", {})["error_code"] == "missing_parameters"
    assert run(_tool, "drop_tables", {})["error_code"] == "unknown_tool"
    # unknown arguments from the LLM are ignored instead of crashing the handler
    assert run(_tool, "get_order_status", {"order_id": "ORD001", "admin": True})["status"] == "shipped"
