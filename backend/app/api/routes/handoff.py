"""Live human handoff and the live conversation feed for the dashboard.

Live feed (/api/conversations/{conversation_id}/live):
  client -> {"type": "auth", "token": "..."}   (must be first)
  server -> READY, then MESSAGE, HUMAN_JOINED, HUMAN_LEFT, CONVERSATION_ENDED as they happen
"""
import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.voice import authenticate_websocket
from app.core.logging import get_logger
from app.core.privacy import for_user
from app.core.security import require_staff
from app.db.database import get_db, async_session_factory
from app.db.models import Conversation
from app.schemas.schemas import MessageCreate
from app.services import live_events
from app.services.handoff_service import HandoffService

logger = get_logger("handoff_routes")
router = APIRouter(prefix="/conversations", tags=["Handoff"])


@router.post("/{conversation_id}/handoff/join")
async def join_conversation(conversation_id: UUID, current_user: dict = Depends(require_staff),
                            db: AsyncSession = Depends(get_db)):
    """Take over from the AI agent: it stops answering until the conversation is handed back."""
    return await HandoffService(db).join(conversation_id, current_user)


@router.post("/{conversation_id}/handoff/messages", status_code=201)
async def send_handoff_message(conversation_id: UUID, data: MessageCreate,
                               current_user: dict = Depends(require_staff), db: AsyncSession = Depends(get_db)):
    """Reply to the customer as the human agent. The customer hears it through their voice session."""
    return await HandoffService(db).send(conversation_id, current_user, data.content)


@router.post("/{conversation_id}/handoff/leave")
async def leave_conversation(conversation_id: UUID, current_user: dict = Depends(require_staff),
                             db: AsyncSession = Depends(get_db)):
    """Hand the conversation back to the AI agent and resolve the escalation."""
    return await HandoffService(db).leave(conversation_id, current_user)


@router.websocket("/{conversation_id}/live")
async def live_feed(ws: WebSocket, conversation_id: str):
    await ws.accept()
    try:
        conv_id = UUID(conversation_id)
    except ValueError:
        await ws.send_json({"type": "ERROR", "data": {"error": "Invalid conversation id", "fatal": True}})
        await ws.close(code=4400)
        return
    user = await authenticate_websocket(ws)
    if not user:
        await ws.send_json({"type": "AUTH_FAILED", "data": {"error": "Invalid or expired token"}})
        await ws.close(code=4401)
        return
    async with async_session_factory() as session:
        conv = await session.get(Conversation, conv_id)
    if not conv:
        await ws.send_json({"type": "ERROR", "data": {"error": "Conversation not found", "fatal": True}})
        await ws.close(code=4404)
        return

    queue = live_events.subscribe(conv_id)
    await ws.send_json({"type": "READY", "data": {"ended": conv.ended_at is not None}})

    async def push():
        while True:
            event, data = await queue.get()
            await ws.send_json({"type": event, "data": for_user(user, data)})
            if event == "CONVERSATION_ENDED":
                return

    async def watch_disconnect():  # the client only listens; reading is how a disconnect is noticed
        while True:
            await ws.receive_text()

    tasks = [asyncio.create_task(push()), asyncio.create_task(watch_disconnect())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        live_events.unsubscribe(conv_id, queue)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await ws.close()
        except Exception:  # already closed by the client
            pass
