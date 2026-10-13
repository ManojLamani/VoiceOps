"""Conversation routes."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.privacy import for_user
from app.core.security import get_current_user
from app.db.database import get_db
from app.db.models import Conversation, Message
from app.db.repositories import (
    ConversationRepository, CustomerRepository, MessageRepository, ToolExecutionRepository, EscalationRepository,
)
from app.schemas.schemas import (
    ConversationCreate, ConversationResponse, ConversationListResponse, ConversationDetailResponse,
    MessageCreate, MessageResponse, ToolExecutionResponse, EscalationResponse,
)
from app.services.agent_service import CustomerSupportAgent

router = APIRouter(prefix="/conversations", tags=["Conversations"])


def conversation_response(c: Conversation, message_count: Optional[int] = None) -> ConversationResponse:
    data = ConversationResponse.model_validate(c)
    if c.customer:
        data.customer_code, data.customer_name = c.customer.customer_id, c.customer.name
    data.message_count = message_count
    return data


def escalation_response(e) -> EscalationResponse:
    data = EscalationResponse.model_validate(e)
    if e.customer:
        data.customer_code, data.customer_name = e.customer.customer_id, e.customer.name
    return data


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(data: ConversationCreate, current_user: dict = Depends(get_current_user),
                              db: AsyncSession = Depends(get_db)):
    """Start a conversation. ``customer_id`` simulates an identified caller (caller ID / logged-in customer)."""
    customer = None
    if data.customer_id:
        customer = await CustomerRepository(db).get_by_customer_id(data.customer_id.upper())
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
    conv = await ConversationRepository(db).create(user_id=UUID(current_user["user_id"]),
                                                   customer_id=customer.id if customer else None)
    conv.channel = data.channel
    conv.metadata_ = {"state": {"customer_uuid": str(customer.id) if customer else None}}
    await db.flush()
    conv = await ConversationRepository(db).get_by_id(conv.id)
    return conversation_response(conv, 0)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                             status: Optional[str] = Query(None, pattern="^(active|completed|escalated|abandoned)$"),
                             current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    repo = ConversationRepository(db)
    conversations = await repo.get_all(skip=skip, limit=limit, status=status)
    counts = dict((await db.execute(
        select(Message.conversation_id, func.count(Message.id))
        .where(Message.conversation_id.in_([c.id for c in conversations]))
        .group_by(Message.conversation_id))).all()) if conversations else {}
    return ConversationListResponse(
        conversations=[conversation_response(c, counts.get(c.id, 0)) for c in conversations],
        total=await repo.count(status=status),
    )


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(conversation_id: UUID, current_user: dict = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    conv = await ConversationRepository(db).get_by_id(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await MessageRepository(db).get_by_conversation(conversation_id)
    executions = await ToolExecutionRepository(db).get_by_conversation(conversation_id)
    escalation = await EscalationRepository(db).get_by_conversation(conversation_id)
    state = (conv.metadata_ or {}).get("state") or {}
    return for_user(current_user, ConversationDetailResponse(
        conversation=conversation_response(conv, len(messages)),
        messages=[MessageResponse.model_validate(m) for m in messages],
        tool_executions=[ToolExecutionResponse.model_validate(e) for e in executions],
        escalation=escalation_response(escalation) if escalation else None,
        state={"active_order_id": state.get("active_order_id"),
               "pending_action": (state.get("pending_action") or {}).get("summary"),
               "failed_turns": state.get("failed_turns", 0), "intents": state.get("intents", [])},
    ))


@router.post("/{conversation_id}/messages")
async def send_message(conversation_id: UUID, data: MessageCreate, current_user: dict = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """Send a text message to the agent (text channel / debugging)."""
    return for_user(current_user, await CustomerSupportAgent(db).process_message(conversation_id, data.content))


@router.post("/{conversation_id}/end")
async def end_conversation(conversation_id: UUID, current_user: dict = Depends(get_current_user),
                           db: AsyncSession = Depends(get_db)):
    """End the conversation and generate/store its summary."""
    return await CustomerSupportAgent(db).end_conversation(conversation_id)
