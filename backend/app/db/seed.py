"""Synthetic seed data: staff users, customers, orders, tickets and knowledge-base documents.

Conversations are intentionally NOT seeded: every conversation, tool execution and metric
in the dashboard comes from the real agent (see scripts/simulate_conversations.py).
"""
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, func

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import hash_password
from app.db.database import async_session_factory, init_db
from app.db.models import (
    User, Customer, Order, Ticket, KnowledgeDocument, OrderStatus, TicketStatus, TicketPriority, AccountStatus,
)
from app.services.rag_service import load_knowledge_files

logger = get_logger("seed")

CUSTOMERS = [
    ("CUST001", "James Wilson", "james.wilson@email.com", "+1-555-0101", "123 Oak Street, Springfield, IL 62701"),
    ("CUST002", "Emily Chen", "emily.chen@email.com", "+1-555-0102", "456 Maple Ave, Austin, TX 78701"),
    ("CUST003", "Michael Brown", "michael.brown@email.com", "+1-555-0103", "789 Pine Road, Seattle, WA 98101"),
    ("CUST004", "Sarah Johnson", "sarah.johnson@email.com", "+1-555-0104", "321 Elm Drive, Denver, CO 80201"),
    ("CUST005", "David Martinez", "david.martinez@email.com", "+1-555-0105", "654 Cedar Lane, Miami, FL 33101"),
    ("CUST006", "Lisa Anderson", "lisa.anderson@email.com", "+1-555-0106", "987 Birch Court, Portland, OR 97201"),
    ("CUST007", "Robert Taylor", "robert.taylor@email.com", "+1-555-0107", "147 Walnut Blvd, Boston, MA 02101"),
    ("CUST008", "Jennifer Garcia", "jennifer.garcia@email.com", "+1-555-0108", "258 Spruce Way, Nashville, TN 37201"),
    ("CUST009", "Priya Patel", "priya.patel@email.com", "+1-555-0109", "36 Harbor View, San Diego, CA 92101"),
    ("CUST010", "Marcus Lee", "marcus.lee@email.com", "+1-555-0110", "910 Lakeshore Dr, Chicago, IL 60601"),
]

P = {
    "headphones": ("ProSound X1 Wireless Headphones", 149.99),
    "lamp": ("SmartDesk LED Lamp", 79.99),
    "cable": ("USB-C Cable Pack", 14.99),
    "watch": ("FitTrack Pro Watch", 199.99),
    "earbuds": ("AirBud Mini Earbuds", 89.99),
    "organizer": ("Desk Organizer Pro", 34.99),
    "case": ("Earbuds Case", 19.99),
    "charger": ("Portable Charger 20000mAh", 49.99),
    "keyboard": ("TypeFlow Mechanical Keyboard", 129.99),
    "band": ("Engraved Watch Band (personalized)", 39.99),
    "speaker": ("EchoBox Smart Speaker", 99.99),
}

# (order_id, customer index, [(product, qty)], ordered days ago, est. delivery offset, delivered days ago,
#  status, tracking, cancellation_eligible, return_eligible, payment_status)
ORDERS = [
    ("ORD001", 0, [("headphones", 1)], 3, 4, None, OrderStatus.SHIPPED, "TRK-2024-001", True, True, "paid"),
    ("ORD002", 0, [("lamp", 1), ("cable", 2)], 15, -8, 9, OrderStatus.DELIVERED, "TRK-2024-002", False, True, "paid"),
    ("ORD003", 1, [("watch", 1)], 1, 6, None, OrderStatus.CONFIRMED, None, True, True, "paid"),
    ("ORD004", 1, [("earbuds", 1)], 20, -13, 14, OrderStatus.DELIVERED, "TRK-2024-004", False, True, "paid"),
    ("ORD005", 2, [("headphones", 2)], 0, 7, None, OrderStatus.PENDING, None, True, True, "paid"),
    ("ORD006", 3, [("lamp", 1), ("organizer", 1)], 5, 2, None, OrderStatus.IN_TRANSIT, "TRK-2024-006", True, True, "paid"),
    ("ORD007", 4, [("watch", 1)], 45, -38, 39, OrderStatus.DELIVERED, "TRK-2024-007", False, True, "paid"),
    ("ORD008", 5, [("earbuds", 2), ("case", 1)], 2, 5, None, OrderStatus.PROCESSING, None, True, True, "paid"),
    ("ORD009", 7, [("headphones", 1)], 10, -3, 4, OrderStatus.DELIVERED, "TRK-2024-009", False, True, "paid"),
    ("ORD010", 0, [("charger", 1)], 7, None, None, OrderStatus.CANCELLED, None, False, False, "refunded"),
    ("ORD011", 2, [("band", 1)], 12, -6, 6, OrderStatus.DELIVERED, "TRK-2024-011", False, False, "paid"),
    ("ORD012", 8, [("keyboard", 1)], 1, 5, None, OrderStatus.CONFIRMED, None, True, True, "paid"),
    ("ORD013", 8, [("speaker", 1)], 9, -2, None, OrderStatus.IN_TRANSIT, "TRK-2024-013", True, True, "paid"),
    ("ORD014", 9, [("charger", 2), ("cable", 1)], 18, -11, 12, OrderStatus.DELIVERED, "TRK-2024-014", False, True, "paid"),
]

TICKETS = [
    ("TKT0001", 0, "Order ORD001 shipping delay",
     "My order ORD001 was supposed to arrive yesterday but tracking shows it's still in transit.",
     "delivery", TicketStatus.OPEN, TicketPriority.HIGH),
    ("TKT0002", 1, "Earbuds fit question",
     "The AirBud Mini Earbuds from order ORD004 don't fit well. Asking about return options.",
     "return", TicketStatus.IN_PROGRESS, TicketPriority.MEDIUM),
    ("TKT0003", 4, "Warranty claim - FitTrack Pro Watch",
     "The heart rate sensor on my FitTrack Pro Watch stopped working after 3 months.",
     "warranty", TicketStatus.OPEN, TicketPriority.HIGH),
    ("TKT0004", 7, "Payment charged twice",
     "I was charged twice for order ORD009. Please investigate and refund the duplicate charge.",
     "payment", TicketStatus.ESCALATED, TicketPriority.URGENT),
]

CATEGORIES = {
    "shipping_policy": "shipping", "return_policy": "returns", "refund_policy": "refunds",
    "cancellation_policy": "cancellations", "payment_faq": "payments", "account_faq": "account",
    "warranty": "warranty", "delivery_info": "delivery", "product_faq": "products",
    "escalation_policy": "escalation",
}


async def sync_knowledge_documents(session) -> int:
    """Insert knowledge-base markdown files that aren't in the database yet (matched by source filename)."""
    existing = {s for (s,) in (await session.execute(select(KnowledgeDocument.source))).all()}
    added = 0
    for doc in load_knowledge_files(get_settings().knowledge_base_dir):
        if doc.source in existing:
            continue
        session.add(KnowledgeDocument(id=uuid4(), title=doc.title, category=CATEGORIES.get(doc.doc_id, "general"),
                                      content=doc.content, source=doc.source))
        added += 1
    await session.flush()
    return added


async def seed_database(reset: bool = False) -> bool:
    """Seed demo data. With reset=True all tables are dropped first. Returns True if data was seeded."""
    await init_db(reset=reset)

    async with async_session_factory() as session:
        if (await session.execute(select(func.count(User.id)))).scalar():
            await sync_knowledge_documents(session)
            await session.commit()
            return False

        now = datetime.utcnow()
        session.add_all([
            User(id=uuid4(), email="admin@voiceops.ai", password_hash=hash_password("admin123"),
                 full_name="Admin User", role="admin"),
            User(id=uuid4(), email="agent@voiceops.ai", password_hash=hash_password("agent123"),
                 full_name="Sarah Mitchell", role="agent"),
        ])

        customers = []
        for i, (cid, name, email, phone, address) in enumerate(CUSTOMERS):
            c = Customer(id=uuid4(), customer_id=cid, name=name, email=email, phone=phone, address=address,
                         account_status=AccountStatus.SUSPENDED if cid == "CUST007" else AccountStatus.ACTIVE,
                         created_at=now - timedelta(days=400 - i * 23))
            customers.append(c)
        session.add_all(customers)
        await session.flush()

        for (oid, ci, items, ordered_ago, eta, delivered_ago, status, tracking, can_cancel, can_return,
             payment) in ORDERS:
            products = [{"name": P[k][0], "quantity": q, "price": P[k][1]} for k, q in items]
            delivered = now - timedelta(days=delivered_ago) if delivered_ago is not None else None
            session.add(Order(
                id=uuid4(), order_id=oid, customer_id=customers[ci].id, products=products,
                total_amount=round(sum(p["price"] * p["quantity"] for p in products), 2),
                order_date=now - timedelta(days=ordered_ago, hours=2),
                estimated_delivery=now + timedelta(days=eta) if eta is not None else None,
                actual_delivery=delivered, status=status, tracking_number=tracking,
                cancellation_eligible=can_cancel, return_eligible=can_return,
                shipping_address=customers[ci].address, payment_status=payment,
                refund_amount=sum(p["price"] * p["quantity"] for p in products) if payment == "refunded" else None,
                return_deadline=delivered + timedelta(days=30) if delivered else None,
                created_at=now - timedelta(days=ordered_ago, hours=2),
            ))

        for tid, ci, subject, desc, category, status, priority in TICKETS:
            session.add(Ticket(id=uuid4(), ticket_id=tid, customer_id=customers[ci].id, subject=subject,
                               description=desc, category=category, status=status, priority=priority))

        added = await sync_knowledge_documents(session)
        await session.commit()
        logger.info("database_seeded", customers=len(CUSTOMERS), orders=len(ORDERS), tickets=len(TICKETS),
                    knowledge_documents=added)
        return True
