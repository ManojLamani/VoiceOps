"""Repository layer for database operations."""
from datetime import datetime
from typing import Optional, List
from uuid import UUID
from datetime import timedelta
from sqlalchemy import select, func, and_, desc, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.db.models import (
    User, Customer, Order, Conversation, Message, ToolExecution,
    Ticket, Escalation, KnowledgeDocument, KnowledgeChunk,
    AgentAnalytics, OrderStatus, ConversationStatus, TicketStatus,
    TicketPriority, MessageRole
)


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: UUID) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def create(self, email: str, password_hash: str, full_name: str, role: str = "agent") -> User:
        user = User(email=email, password_hash=password_hash, full_name=full_name, role=role)
        self.db.add(user)
        await self.db.flush()
        return user


class CustomerRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all(self, skip: int = 0, limit: int = 50) -> List[Customer]:
        result = await self.db.execute(
            select(Customer).order_by(Customer.created_at.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_id(self, customer_uuid: UUID) -> Optional[Customer]:
        result = await self.db.execute(select(Customer).where(Customer.id == customer_uuid))
        return result.scalar_one_or_none()

    async def get_by_customer_id(self, customer_id: str) -> Optional[Customer]:
        result = await self.db.execute(
            select(Customer).where(Customer.customer_id == customer_id)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[Customer]:
        result = await self.db.execute(select(Customer).where(Customer.email == email))
        return result.scalar_one_or_none()

    async def update(self, customer: Customer, **kwargs) -> Customer:
        for key, value in kwargs.items():
            if hasattr(customer, key) and value is not None:
                setattr(customer, key, value)
        customer.updated_at = datetime.utcnow()
        await self.db.flush()
        return customer

    async def count(self) -> int:
        result = await self.db.execute(select(func.count(Customer.id)))
        return result.scalar()


class OrderRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all(self, skip: int = 0, limit: int = 50) -> List[Order]:
        result = await self.db.execute(
            select(Order).order_by(Order.created_at.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_id(self, order_uuid: UUID) -> Optional[Order]:
        result = await self.db.execute(select(Order).where(Order.id == order_uuid))
        return result.scalar_one_or_none()

    async def get_by_order_id(self, order_id: str) -> Optional[Order]:
        result = await self.db.execute(
            select(Order).where(Order.order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def get_by_customer(self, customer_uuid: UUID) -> List[Order]:
        result = await self.db.execute(
            select(Order).where(Order.customer_id == customer_uuid).order_by(Order.order_date.desc())
        )
        return list(result.scalars().all())

    async def update_status(self, order: Order, status: OrderStatus, **kwargs) -> Order:
        order.status = status
        for key, value in kwargs.items():
            if hasattr(order, key):
                setattr(order, key, value)
        order.updated_at = datetime.utcnow()
        await self.db.flush()
        return order

    async def count(self) -> int:
        result = await self.db.execute(select(func.count(Order.id)))
        return result.scalar()


class ConversationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user_id: UUID, customer_id: UUID = None) -> Conversation:
        conv = Conversation(user_id=user_id, customer_id=customer_id)
        self.db.add(conv)
        await self.db.flush()
        return conv

    async def get_by_id(self, conv_id: UUID) -> Optional[Conversation]:
        result = await self.db.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .options(selectinload(Conversation.tool_executions))
            .options(selectinload(Conversation.escalation))
            .options(selectinload(Conversation.customer))
            .where(Conversation.id == conv_id)
        )
        return result.scalar_one_or_none()

    async def get_by_user(self, user_id: UUID, skip: int = 0, limit: int = 50) -> List[Conversation]:
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.created_at.desc())
            .offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def get_all(self, skip: int = 0, limit: int = 50, status: str = None) -> List[Conversation]:
        query = select(Conversation).options(selectinload(Conversation.customer))
        if status:
            query = query.where(Conversation.status == ConversationStatus(status))
        result = await self.db.execute(
            query.order_by(Conversation.created_at.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def count(self, user_id: UUID = None, status: str = None) -> int:
        query = select(func.count(Conversation.id))
        if user_id:
            query = query.where(Conversation.user_id == user_id)
        if status:
            query = query.where(Conversation.status == ConversationStatus(status))
        result = await self.db.execute(query)
        return result.scalar()

    async def count_by_status(self, status: ConversationStatus) -> int:
        result = await self.db.execute(
            select(func.count(Conversation.id)).where(Conversation.status == status)
        )
        return result.scalar()

    async def update(self, conv: Conversation, **kwargs) -> Conversation:
        for key, value in kwargs.items():
            if hasattr(conv, key) and value is not None:
                setattr(conv, key, value)
        conv.updated_at = datetime.utcnow()
        await self.db.flush()
        return conv


class MessageRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, conversation_id: UUID, role: MessageRole, content: str, **kwargs) -> Message:
        msg = Message(conversation_id=conversation_id, role=role, content=content, **kwargs)
        self.db.add(msg)
        await self.db.flush()
        return msg

    async def get_by_conversation(self, conversation_id: UUID) -> List[Message]:
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        return list(result.scalars().all())

    async def count_by_conversation(self, conversation_id: UUID) -> int:
        result = await self.db.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conversation_id)
        )
        return result.scalar()


class ToolExecutionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, conversation_id: UUID, tool_name: str, tool_args: dict,
                     tool_result: dict = None, success: bool = True,
                     error_message: str = None, execution_time_ms: float = None,
                     message_id: UUID = None) -> ToolExecution:
        te = ToolExecution(
            conversation_id=conversation_id,
            message_id=message_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            success=success,
            error_message=error_message,
            execution_time_ms=execution_time_ms,
        )
        self.db.add(te)
        await self.db.flush()
        return te

    async def get_by_conversation(self, conversation_id: UUID) -> List[ToolExecution]:
        result = await self.db.execute(
            select(ToolExecution)
            .where(ToolExecution.conversation_id == conversation_id)
            .order_by(ToolExecution.created_at)
        )
        return list(result.scalars().all())

    async def count_all(self) -> int:
        result = await self.db.execute(select(func.count(ToolExecution.id)))
        return result.scalar()

    async def count_successful(self) -> int:
        result = await self.db.execute(
            select(func.count(ToolExecution.id)).where(ToolExecution.success == True)
        )
        return result.scalar()


class TicketRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, ticket_id: str, customer_id: UUID, subject: str,
                     description: str, category: str = None,
                     priority: str = "medium", conversation_id: UUID = None) -> Ticket:
        ticket = Ticket(
            ticket_id=ticket_id,
            customer_id=customer_id,
            conversation_id=conversation_id,
            subject=subject,
            description=description,
            category=category,
            priority=TicketPriority(priority) if priority in TicketPriority._value2member_map_ else TicketPriority.MEDIUM,
        )
        self.db.add(ticket)
        await self.db.flush()
        return ticket

    async def get_by_id(self, ticket_uuid: UUID) -> Optional[Ticket]:
        result = await self.db.execute(select(Ticket).where(Ticket.id == ticket_uuid))
        return result.scalar_one_or_none()

    async def get_by_ticket_id(self, ticket_id: str) -> Optional[Ticket]:
        result = await self.db.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        return result.scalar_one_or_none()

    async def get_all(self, skip: int = 0, limit: int = 50) -> List[Ticket]:
        result = await self.db.execute(
            select(Ticket).order_by(Ticket.created_at.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def update(self, ticket: Ticket, **kwargs) -> Ticket:
        for key, value in kwargs.items():
            if hasattr(ticket, key) and value is not None:
                setattr(ticket, key, value)
        ticket.updated_at = datetime.utcnow()
        await self.db.flush()
        return ticket

    async def count(self) -> int:
        result = await self.db.execute(select(func.count(Ticket.id)))
        return result.scalar()

    async def next_ticket_id(self) -> str:
        result = await self.db.execute(select(Ticket.ticket_id))
        numbers = [int(t[3:]) for (t,) in result.all() if t and t[3:].isdigit()]
        return f"TKT{(max(numbers, default=0) + 1):04d}"


class EscalationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, conversation_id: UUID, reason: str,
                     details: str = None, customer_summary: str = None,
                     conversation_summary: str = None, tools_used: list = None,
                     customer_id: UUID = None, ticket_id: str = None) -> Escalation:
        esc = Escalation(
            conversation_id=conversation_id,
            customer_id=customer_id,
            ticket_id=ticket_id,
            reason=reason,
            details=details,
            customer_summary=customer_summary,
            conversation_summary=conversation_summary,
            tools_used=tools_used,
        )
        self.db.add(esc)
        await self.db.flush()
        return esc

    async def get_by_conversation(self, conversation_id: UUID) -> Optional[Escalation]:
        result = await self.db.execute(
            select(Escalation).options(selectinload(Escalation.customer))
            .where(Escalation.conversation_id == conversation_id)
        )
        return result.scalar_one_or_none()

    async def get_all(self, skip: int = 0, limit: int = 50) -> List[Escalation]:
        result = await self.db.execute(
            select(Escalation).options(selectinload(Escalation.customer))
            .order_by(Escalation.created_at.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def count(self) -> int:
        result = await self.db.execute(select(func.count(Escalation.id)))
        return result.scalar()


class KnowledgeRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_document(self, title: str, category: str, content: str,
                               source: str = None) -> KnowledgeDocument:
        doc = KnowledgeDocument(title=title, category=category, content=content, source=source)
        self.db.add(doc)
        await self.db.flush()
        return doc

    async def get_document(self, doc_id: UUID) -> Optional[KnowledgeDocument]:
        result = await self.db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id)
        )
        return result.scalar_one_or_none()

    async def get_all_documents(self, skip: int = 0, limit: int = 50) -> List[KnowledgeDocument]:
        result = await self.db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.is_active == True)
            .order_by(KnowledgeDocument.created_at.desc())
            .offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def create_chunk(self, document_id: UUID, chunk_index: int,
                            content: str, embedding_id: str = None,
                            token_count: int = None) -> KnowledgeChunk:
        chunk = KnowledgeChunk(
            document_id=document_id,
            chunk_index=chunk_index,
            content=content,
            embedding_id=embedding_id,
            token_count=token_count,
        )
        self.db.add(chunk)
        await self.db.flush()
        return chunk

    async def get_chunks_by_document(self, document_id: UUID) -> List[KnowledgeChunk]:
        result = await self.db.execute(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
            .order_by(KnowledgeChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def get_chunk_by_embedding_id(self, embedding_id: str) -> Optional[KnowledgeChunk]:
        result = await self.db.execute(
            select(KnowledgeChunk).where(KnowledgeChunk.embedding_id == embedding_id)
        )
        return result.scalar_one_or_none()

    async def get_all_chunks(self) -> List[KnowledgeChunk]:
        result = await self.db.execute(
            select(KnowledgeChunk).order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def count_documents(self) -> int:
        result = await self.db.execute(
            select(func.count(KnowledgeDocument.id)).where(KnowledgeDocument.is_active == True)
        )
        return result.scalar()

    async def update_document(self, doc: KnowledgeDocument, **kwargs) -> KnowledgeDocument:
        for key, value in kwargs.items():
            if hasattr(doc, key) and value is not None:
                setattr(doc, key, value)
        doc.updated_at = datetime.utcnow()
        await self.db.flush()
        return doc


class AnalyticsRepository:
    """All analytics are computed from stored conversations, messages and tool executions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _scalar(self, query):
        return (await self.db.execute(query)).scalar()

    async def get_overview(self) -> dict:
        total = await self._scalar(select(func.count(Conversation.id)))
        resolved = await self._scalar(
            select(func.count(Conversation.id)).where(Conversation.resolution == "resolved"))
        unresolved = await self._scalar(
            select(func.count(Conversation.id)).where(Conversation.resolution == "unresolved"))
        escalated = await self._scalar(
            select(func.count(Conversation.id)).where(Conversation.status == ConversationStatus.ESCALATED))
        active = await self._scalar(
            select(func.count(Conversation.id)).where(Conversation.status == ConversationStatus.ACTIVE))
        avg_dur = await self._scalar(
            select(func.avg(Conversation.duration_seconds)).where(Conversation.duration_seconds.isnot(None))) or 0
        avg_resp = await self._scalar(
            select(func.avg(Message.processing_time_ms))
            .where(Message.role == MessageRole.ASSISTANT, Message.processing_time_ms.isnot(None))) or 0
        total_tools = await self._scalar(select(func.count(ToolExecution.id)))
        ok_tools = await self._scalar(select(func.count(ToolExecution.id)).where(ToolExecution.success.is_(True)))
        total_messages = await self._scalar(select(func.count(Message.id)))
        finished = resolved + unresolved + escalated
        return {
            "total_conversations": total,
            "active_conversations": active,
            "resolved_conversations": resolved,
            "unresolved_conversations": unresolved,
            "escalated_conversations": escalated,
            "total_messages": total_messages,
            "avg_response_time_ms": round(float(avg_resp), 2),
            "avg_duration_seconds": round(float(avg_dur), 2),
            "resolution_rate": round(resolved / finished * 100, 2) if finished else 0.0,
            "escalation_rate": round(escalated / finished * 100, 2) if finished else 0.0,
            "total_tool_calls": total_tools,
            "tool_success_rate": round(ok_tools / total_tools * 100, 2) if total_tools else 0.0,
        }

    async def get_intent_distribution(self) -> List[dict]:
        result = await self.db.execute(
            select(Conversation.primary_intent, func.count(Conversation.id).label("cnt"))
            .where(Conversation.primary_intent.isnot(None))
            .group_by(Conversation.primary_intent)
            .order_by(desc("cnt"))
        )
        rows = result.all()
        total = sum(r[1] for r in rows)
        return [
            {"intent": r[0], "count": r[1], "percentage": round(r[1] / max(total, 1) * 100, 2)}
            for r in rows
        ]

    async def get_conversations_over_time(self, days: int = 30) -> List[dict]:
        cutoff = datetime.utcnow() - timedelta(days=days - 1)
        result = await self.db.execute(
            select(Conversation.created_at, Conversation.status, Conversation.resolution)
            .where(Conversation.created_at >= cutoff)
        )
        buckets = {}
        for i in range(days):
            day = (cutoff + timedelta(days=i)).date().isoformat()
            buckets[day] = {"date": day, "conversations": 0, "resolved": 0, "escalated": 0}
        for created_at, status, resolution in result.all():
            b = buckets.get(created_at.date().isoformat())
            if not b:
                continue
            b["conversations"] += 1
            if resolution == "resolved":
                b["resolved"] += 1
            elif status == ConversationStatus.ESCALATED:
                b["escalated"] += 1
        return list(buckets.values())

    async def get_tool_usage_stats(self) -> List[dict]:
        result = await self.db.execute(
            select(
                ToolExecution.tool_name,
                func.count(ToolExecution.id).label("total"),
                func.sum(case((ToolExecution.success.is_(True), 1), else_=0)).label("successes"),
                func.avg(ToolExecution.execution_time_ms).label("avg_time"),
            ).group_by(ToolExecution.tool_name).order_by(desc("total"))
        )
        return [
            {
                "tool_name": r[0],
                "total_calls": r[1],
                "success_count": int(r[2] or 0),
                "failure_count": r[1] - int(r[2] or 0),
                "avg_execution_time_ms": round(float(r[3] or 0), 2),
            }
            for r in result.all()
        ]

    async def get_response_latency(self, limit: int = 50) -> List[dict]:
        """Most recent assistant turn latencies (oldest first)."""
        result = await self.db.execute(
            select(Message.created_at, Message.processing_time_ms)
            .where(Message.role == MessageRole.ASSISTANT, Message.processing_time_ms.isnot(None))
            .order_by(Message.created_at.desc()).limit(limit)
        )
        rows = list(reversed(result.all()))
        return [{"timestamp": r[0].isoformat(), "latency_ms": round(float(r[1]), 1)} for r in rows]

    async def get_escalation_reasons(self) -> List[dict]:
        result = await self.db.execute(
            select(Escalation.reason, func.count(Escalation.id)).group_by(Escalation.reason)
        )
        return [{"reason": (r[0].value if hasattr(r[0], "value") else str(r[0])), "count": r[1]}
                for r in result.all()]

    async def get_sentiment_distribution(self) -> List[dict]:
        result = await self.db.execute(
            select(Conversation.sentiment, func.count(Conversation.id))
            .where(Conversation.sentiment.isnot(None)).group_by(Conversation.sentiment)
        )
        return [{"sentiment": r[0], "count": r[1]} for r in result.all()]
