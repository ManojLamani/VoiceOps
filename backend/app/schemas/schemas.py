"""Pydantic schemas for API request/response validation."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, EmailStr, Field
from uuid import UUID


# ── Auth Schemas ──

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=1, max_length=255)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ── Customer Schemas ──

class CustomerResponse(BaseModel):
    id: UUID
    customer_id: str
    name: str
    email: str
    phone: Optional[str] = None
    account_status: str
    address: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class CustomerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, pattern=r"^\+?[\d\s().-]{7,20}$")
    address: Optional[str] = Field(None, min_length=1, max_length=500)


# ── Order Schemas ──

class OrderResponse(BaseModel):
    id: UUID
    order_id: str
    customer_id: UUID
    products: List[Dict[str, Any]]
    total_amount: float
    order_date: datetime
    estimated_delivery: Optional[datetime] = None
    actual_delivery: Optional[datetime] = None
    status: str
    shipping_address: Optional[str] = None
    tracking_number: Optional[str] = None
    cancellation_eligible: bool
    return_eligible: bool
    return_deadline: Optional[datetime] = None
    payment_status: Optional[str] = None
    refund_amount: Optional[float] = None
    notes: Optional[str] = None
    customer_code: Optional[str] = None
    customer_name: Optional[str] = None
    can_cancel: Optional[bool] = None
    can_return: Optional[bool] = None
    created_at: datetime

    class Config:
        from_attributes = True


class OrderCancelRequest(BaseModel):
    reason: Optional[str] = None


class ReturnRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    items: Optional[List[str]] = None


# ── Conversation Schemas ──

class ConversationCreate(BaseModel):
    customer_id: Optional[str] = None  # caller already identified (e.g. CUST002); None = unverified caller
    channel: str = Field("voice", pattern="^(voice|text)$")


class ConversationResponse(BaseModel):
    id: UUID
    user_id: UUID
    customer_id: Optional[UUID] = None
    status: str
    primary_intent: Optional[str] = None
    resolution: Optional[str] = None
    summary: Optional[str] = None
    summary_details: Optional[Dict[str, Any]] = None
    sentiment: Optional[str] = None
    channel: Optional[str] = None
    customer_code: Optional[str] = None
    customer_name: Optional[str] = None
    message_count: Optional[int] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationListResponse(BaseModel):
    conversations: List[ConversationResponse]
    total: int


class ConversationEndRequest(BaseModel):
    pass


# ── Message Schemas ──

MAX_MESSAGE_CHARS = 2000  # same limit on REST, the REST voice turn and the WebSocket


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=MAX_MESSAGE_CHARS)


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    role: str
    content: str
    intent: Optional[str] = None
    confidence: Optional[float] = None
    rag_sources: Optional[List[Dict[str, Any]]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    processing_time_ms: Optional[float] = None
    author_id: Optional[UUID] = None  # staff member who wrote it (live handoff); None for the AI and the customer
    author_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ── Ticket Schemas ──

class TicketCreate(BaseModel):
    customer_id: str = Field(..., max_length=50)
    subject: str = Field(..., min_length=1, max_length=480)
    description: str = Field(..., min_length=1, max_length=5000)
    category: Optional[str] = Field(None, max_length=100)
    priority: Optional[str] = Field("medium", pattern="^(low|medium|high|urgent)$")


class TicketUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern="^(open|in_progress|waiting_customer|escalated|resolved|closed)$")
    priority: Optional[str] = Field(None, pattern="^(low|medium|high|urgent)$")
    resolution: Optional[str] = Field(None, max_length=5000)
    assigned_to: Optional[UUID] = None


class TicketResponse(BaseModel):
    id: UUID
    ticket_id: str
    customer_id: UUID
    conversation_id: Optional[UUID] = None
    subject: str
    description: str
    category: Optional[str] = None
    status: str
    priority: str
    resolution: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ── Escalation Schemas ──

class EscalationCreate(BaseModel):
    conversation_id: UUID
    reason: str
    details: Optional[str] = None


class EscalationResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    reason: str
    details: Optional[str] = None
    customer_summary: Optional[str] = None
    conversation_summary: Optional[str] = None
    tools_used: Optional[List[Dict[str, Any]]] = None
    ticket_id: Optional[str] = None
    customer_code: Optional[str] = None
    customer_name: Optional[str] = None
    status: str
    resolved_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class EscalationUpdate(BaseModel):
    status: str = Field(..., pattern="^(pending|assigned|resolved)$")


class ToolExecutionResponse(BaseModel):
    id: UUID
    message_id: Optional[UUID] = None
    tool_name: str
    tool_args: Dict[str, Any]
    tool_result: Optional[Dict[str, Any]] = None
    success: bool
    error_message: Optional[str] = None
    execution_time_ms: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationDetailResponse(BaseModel):
    conversation: ConversationResponse
    messages: List[MessageResponse]
    tool_executions: List[ToolExecutionResponse]
    escalation: Optional[EscalationResponse] = None
    state: Dict[str, Any] = {}


# ── Knowledge Schemas ──

class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    category: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1, max_length=100_000)
    source: Optional[str] = None


class KnowledgeDocumentResponse(BaseModel):
    id: UUID
    title: str
    category: str
    content: str
    source: Optional[str] = None
    is_active: bool
    chunk_count: int
    last_indexed_at: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ── Analytics Schemas ──

class AnalyticsOverview(BaseModel):
    total_conversations: int
    active_conversations: int
    unresolved_conversations: int
    total_messages: int
    resolved_conversations: int
    escalated_conversations: int
    avg_response_time_ms: float
    avg_duration_seconds: float
    resolution_rate: float
    escalation_rate: float
    total_tool_calls: int
    tool_success_rate: float


class AnalyticsTimeSeries(BaseModel):
    date: str
    conversations: int
    resolved: int
    escalated: int


class IntentDistribution(BaseModel):
    intent: str
    count: int
    percentage: float


class ToolUsageStats(BaseModel):
    tool_name: str
    total_calls: int
    success_count: int
    failure_count: int
    avg_execution_time_ms: float
