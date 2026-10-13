"""Live human handoff: a staff member joins an active conversation, talks to the customer, and hands back.

While a human is handling the conversation (``state["human_agent"]``), the AI agent stays silent: the
customer's messages are stored and relayed to the human (see CustomerSupportAgent.process_message), and
the human's replies are delivered to the customer's voice session and spoken with TTS.
"""
from datetime import datetime
from typing import Any, Dict, Tuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, MessageRole
from app.db.repositories import ConversationRepository, MessageRepository, EscalationRepository
from app.services.agent_service import ConversationNotFound, ConversationClosed
from app.services.live_events import publish_after_commit, message_event


class HandoffConflict(Exception):
    """The request conflicts with who is (or isn't) handling the conversation."""


class HandoffService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.conversations = ConversationRepository(db)
        self.messages = MessageRepository(db)
        self.escalations = EscalationRepository(db)

    async def join(self, conversation_id: UUID, staff: Dict[str, Any]) -> Dict[str, Any]:
        conversation, state = await self._load(conversation_id)
        human = state.get("human_agent")
        if human and human["user_id"] != staff["user_id"]:
            raise HandoffConflict(f"{human['name']} is already handling this conversation.")
        if not human:
            human = {"user_id": staff["user_id"], "name": staff["full_name"],
                     "joined_at": datetime.utcnow().isoformat()}
            state["human_agent"] = human
            state["pending_action"] = None  # an action the AI asked the customer to confirm no longer applies
            self._save_state(conversation, state)
            escalation = await self.escalations.get_by_conversation(conversation_id)
            if escalation and escalation.status != "resolved":
                escalation.status, escalation.assigned_to = "assigned", UUID(staff["user_id"])
            await self._system_message(conversation_id, f"{human['name']} joined the conversation.")
            publish_after_commit(self.db, conversation_id, "HUMAN_JOINED", {"agent_name": human["name"]})
        return {"human_agent": {"name": human["name"], "joined_at": human["joined_at"]}}

    async def send(self, conversation_id: UUID, staff: Dict[str, Any], content: str) -> Dict[str, Any]:
        conversation, state = await self._load(conversation_id)
        human = state.get("human_agent")
        if not human or human["user_id"] != staff["user_id"]:
            raise HandoffConflict("Join the conversation before replying to the customer.")
        message = await self.messages.create(conversation_id=conversation_id, role=MessageRole.ASSISTANT,
                                             content=content.strip(), author_id=UUID(staff["user_id"]))
        payload = message_event(message, author_name=human["name"])
        publish_after_commit(self.db, conversation_id, "MESSAGE", payload)
        return payload

    async def leave(self, conversation_id: UUID, staff: Dict[str, Any]) -> Dict[str, Any]:
        """Hand the conversation back to the AI agent and resolve the escalation."""
        conversation, state = await self._load(conversation_id)
        human = state.get("human_agent")
        if not human:
            raise HandoffConflict("No one is handling this conversation.")
        if human["user_id"] != staff["user_id"] and staff.get("role") != "admin":
            raise HandoffConflict(f"{human['name']} is handling this conversation.")
        # The human dealt with the issue: the AI resumes normally instead of repeating the hand-off message.
        state.update(human_agent=None, escalated=False, failed_turns=0, negative_turns=0)
        self._save_state(conversation, state)
        escalation = await self.escalations.get_by_conversation(conversation_id)
        if escalation and escalation.status != "resolved":
            escalation.status, escalation.resolved_at = "resolved", datetime.utcnow()
        await self._system_message(conversation_id, f"{human['name']} handed the conversation back to Ava.")
        publish_after_commit(self.db, conversation_id, "HUMAN_LEFT", {"agent_name": human["name"]})
        return {"human_agent": None}

    async def _load(self, conversation_id: UUID) -> Tuple[Conversation, Dict[str, Any]]:
        conversation = await self.conversations.get_by_id(conversation_id)
        if not conversation:
            raise ConversationNotFound(str(conversation_id))
        if conversation.ended_at is not None:
            raise ConversationClosed(str(conversation_id))
        return conversation, dict((conversation.metadata_ or {}).get("state") or {})

    @staticmethod
    def _save_state(conversation: Conversation, state: Dict[str, Any]):
        conversation.metadata_ = {**(conversation.metadata_ or {}), "state": state}

    async def _system_message(self, conversation_id: UUID, content: str):
        message = await self.messages.create(conversation_id=conversation_id, role=MessageRole.SYSTEM,
                                             content=content)
        publish_after_commit(self.db, conversation_id, "MESSAGE", message_event(message))
