"""SQLAlchemy models for VoiceOps."""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean, DateTime,
    ForeignKey, Enum, JSON, Index, TypeDecorator
)
from sqlalchemy.orm import relationship
from app.db.database import Base
import enum


class GUID(TypeDecorator):
    """Platform-independent GUID type. Uses String(36) for SQLite compatibility."""
    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return str(value)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return uuid.UUID(value) if not isinstance(value, uuid.UUID) else value
        return value


# ── Enums ──

class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURN_REQUESTED = "return_requested"
    RETURNED = "returned"
    REFUNDED = "refunded"


class TicketStatus(str, enum.Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_CUSTOMER = "waiting_customer"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ConversationStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class EscalationReason(str, enum.Enum):
    CUSTOMER_REQUEST = "customer_request"
    LOW_CONFIDENCE = "low_confidence"
    REPEATED_FAILURE = "repeated_failure"
    SENSITIVE_ISSUE = "sensitive_issue"
    OUTSIDE_SCOPE = "outside_scope"
    CUSTOMER_DISSATISFIED = "customer_dissatisfied"
    POLICY_REQUIRED = "policy_required"


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


# ── Models ──

class User(Base):
    """Application user (admin/agent)."""
    __tablename__ = "users"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(String(50), default="agent")  # admin, agent
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversations = relationship("Conversation", back_populates="user")


class Customer(Base):
    """Customer record."""
    __tablename__ = "customers"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    customer_id = Column(String(50), unique=True, nullable=False, index=True)  # e.g. CUST001
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(50))
    account_status = Column(Enum(AccountStatus), default=AccountStatus.ACTIVE)
    address = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    orders = relationship("Order", back_populates="customer")
    tickets = relationship("Ticket", back_populates="customer")
    conversations = relationship("Conversation", back_populates="customer")


class Order(Base):
    """Customer order."""
    __tablename__ = "orders"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    order_id = Column(String(50), unique=True, nullable=False, index=True)  # e.g. ORD001
    customer_id = Column(GUID, ForeignKey("customers.id"), nullable=False)
    products = Column(JSON, nullable=False)  # [{name, quantity, price}]
    total_amount = Column(Float, nullable=False)
    order_date = Column(DateTime, nullable=False)
    estimated_delivery = Column(DateTime)
    actual_delivery = Column(DateTime)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING)
    shipping_address = Column(Text)
    tracking_number = Column(String(100))
    cancellation_eligible = Column(Boolean, default=True)
    return_eligible = Column(Boolean, default=False)
    return_deadline = Column(DateTime)
    payment_method = Column(String(50), default="card")
    payment_status = Column(String(50), default="paid")  # paid, refund_pending, refunded
    refund_amount = Column(Float)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="orders")

    __table_args__ = (
        Index("ix_orders_customer_status", "customer_id", "status"),
    )


class Conversation(Base):
    """Voice/chat conversation session."""
    __tablename__ = "conversations"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    user_id = Column(GUID, ForeignKey("users.id"), nullable=False)
    customer_id = Column(GUID, ForeignKey("customers.id"), nullable=True)
    status = Column(Enum(ConversationStatus), default=ConversationStatus.ACTIVE)
    primary_intent = Column(String(100))
    resolution = Column(Text)
    summary = Column(Text)
    sentiment = Column(String(50))  # positive, neutral, negative
    summary_details = Column(JSON)  # {issue, intent, resolution, actions_taken, tools_used, follow_up, outcome}
    channel = Column(String(20), default="voice")  # voice, text
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime)
    duration_seconds = Column(Float)
    metadata_ = Column("metadata", JSON, default=dict)  # agent state: customer, active order, pending action
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="conversations")
    customer = relationship("Customer", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", order_by="Message.created_at")
    tool_executions = relationship("ToolExecution", back_populates="conversation")
    escalation = relationship("Escalation", back_populates="conversation", uselist=False)

    __table_args__ = (
        Index("ix_conversations_user_status", "user_id", "status"),
    )


class Message(Base):
    """Individual message in a conversation."""
    __tablename__ = "messages"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    conversation_id = Column(GUID, ForeignKey("conversations.id"), nullable=False)
    role = Column(Enum(MessageRole), nullable=False)
    content = Column(Text, nullable=False)
    intent = Column(String(100))
    confidence = Column(Float)
    audio_url = Column(String(500))
    rag_sources = Column(JSON)  # [{doc_id, chunk_id, score}]
    tool_calls = Column(JSON)  # [{tool_name, args, result}]
    processing_time_ms = Column(Float)
    author_id = Column(GUID, ForeignKey("users.id"), nullable=True)  # staff member who wrote it (live handoff)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")
    author = relationship("User")

    @property
    def author_name(self):
        return self.author.full_name if self.author_id and self.author else None

    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )


class ToolExecution(Base):
    """Record of a tool being executed during a conversation."""
    __tablename__ = "tool_executions"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    conversation_id = Column(GUID, ForeignKey("conversations.id"), nullable=False)
    message_id = Column(GUID, ForeignKey("messages.id"), nullable=True)
    tool_name = Column(String(100), nullable=False)
    tool_args = Column(JSON, nullable=False)
    tool_result = Column(JSON)
    success = Column(Boolean, default=True)
    error_message = Column(Text)
    execution_time_ms = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="tool_executions")


class Ticket(Base):
    """Customer support ticket."""
    __tablename__ = "tickets"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    ticket_id = Column(String(50), unique=True, nullable=False, index=True)  # e.g. TKT001
    customer_id = Column(GUID, ForeignKey("customers.id"), nullable=False)
    conversation_id = Column(GUID, ForeignKey("conversations.id"), nullable=True)
    subject = Column(String(500), nullable=False)
    description = Column(Text, nullable=False)
    category = Column(String(100))
    status = Column(Enum(TicketStatus), default=TicketStatus.OPEN)
    priority = Column(Enum(TicketPriority), default=TicketPriority.MEDIUM)
    assigned_to = Column(GUID, ForeignKey("users.id"), nullable=True)
    resolution = Column(Text)
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer = relationship("Customer", back_populates="tickets")


class Escalation(Base):
    """Escalation to human agent."""
    __tablename__ = "escalations"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    conversation_id = Column(GUID, ForeignKey("conversations.id"), nullable=False)
    customer_id = Column(GUID, ForeignKey("customers.id"), nullable=True)
    ticket_id = Column(String(50))
    reason = Column(Enum(EscalationReason), nullable=False)
    details = Column(Text)
    customer_summary = Column(Text)
    conversation_summary = Column(Text)
    tools_used = Column(JSON)
    assigned_to = Column(GUID, ForeignKey("users.id"), nullable=True)
    status = Column(String(50), default="pending")  # pending, assigned, resolved
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="escalation")
    customer = relationship("Customer")


class KnowledgeDocument(Base):
    """Knowledge base document."""
    __tablename__ = "knowledge_documents"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    title = Column(String(500), nullable=False)
    category = Column(String(100), nullable=False)
    content = Column(Text, nullable=False)
    source = Column(String(500))
    is_active = Column(Boolean, default=True)
    chunk_count = Column(Integer, default=0)
    last_indexed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class KnowledgeChunk(Base):
    """Chunked piece of a knowledge document for RAG."""
    __tablename__ = "knowledge_chunks"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    document_id = Column(GUID, ForeignKey("knowledge_documents.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding_id = Column(String(100))  # Reference in vector store
    token_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("KnowledgeDocument")

    __table_args__ = (
        Index("ix_chunks_document", "document_id", "chunk_index"),
    )


class AgentAnalytics(Base):
    """Aggregated analytics data."""
    __tablename__ = "agent_analytics"

    id = Column(GUID, primary_key=True, default=uuid.uuid4)
    date = Column(DateTime, nullable=False, index=True)
    total_conversations = Column(Integer, default=0)
    resolved_conversations = Column(Integer, default=0)
    escalated_conversations = Column(Integer, default=0)
    avg_response_time_ms = Column(Float, default=0)
    avg_duration_seconds = Column(Float, default=0)
    total_tool_calls = Column(Integer, default=0)
    successful_tool_calls = Column(Integer, default=0)
    intents = Column(JSON, default=dict)  # {intent: count}
    created_at = Column(DateTime, default=datetime.utcnow)
