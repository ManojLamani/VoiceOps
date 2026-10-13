"""Housekeeping: close conversations whose client or server went away mid-call."""
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, func

from app.core.logging import get_logger
from app.db.database import async_session_factory
from app.db.models import Conversation, ConversationStatus, Message

logger = get_logger("maintenance")

STALE_AFTER_MINUTES = 30
SWEEP_INTERVAL_SECONDS = 300


async def end_stale_conversations(idle_minutes: int = STALE_AFTER_MINUTES, now: Optional[datetime] = None) -> int:
    """End active conversations with no activity for ``idle_minutes``.

    Covers a backend crash/restart (in-memory auto-end timers are lost) and REST clients that simply
    disappear. The summary/outcome logic is the normal one; the end time is the last activity, so
    durations in analytics aren't inflated by the time the conversation sat idle.
    """
    from app.services.agent_service import CustomerSupportAgent

    now = now or datetime.utcnow()
    cutoff = now - timedelta(minutes=idle_minutes)
    async with async_session_factory() as session:
        last_message = (select(Message.conversation_id, func.max(Message.created_at).label("last"))
                        .group_by(Message.conversation_id).subquery())
        rows = (await session.execute(
            select(Conversation.id, func.coalesce(last_message.c.last, Conversation.started_at))
            .outerjoin(last_message, last_message.c.conversation_id == Conversation.id)
            .where(Conversation.status == ConversationStatus.ACTIVE, Conversation.ended_at.is_(None))
        )).all()
    stale = [(cid, last) for cid, last in rows if last < cutoff]
    for conversation_id, last_activity in stale:
        async with async_session_factory() as session:
            try:
                await CustomerSupportAgent(session).end_conversation(conversation_id, ended_at=last_activity)
                await session.commit()
            except Exception as e:  # one bad record must not stop the sweep
                await session.rollback()
                logger.warning("stale_end_failed", conversation_id=str(conversation_id), error=str(e))
    if stale:
        logger.info("stale_conversations_ended", count=len(stale))
    return len(stale)


async def sweep_forever():
    while True:
        try:
            await end_stale_conversations()
        except Exception as e:
            logger.warning("stale_sweep_failed", error=str(e))
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
