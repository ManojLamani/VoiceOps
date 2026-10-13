"""Live conversation events: the dashboard's live transcript and the human handoff channel.

Events are queued on the database session and published only after it commits, so subscribers never
see a message that was rolled back. Subscribers live in this process; running several backend workers
would need a shared broker (e.g. Redis pub/sub) behind publish().

Events: MESSAGE (a stored message), HUMAN_JOINED, HUMAN_LEFT, CONVERSATION_ENDED.
"""
import asyncio
from collections import defaultdict
from typing import Any, Dict, Optional, Set

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models import Message

QUEUE_SIZE = 200
_subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)


def subscribe(conversation_id) -> asyncio.Queue:
    queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    _subscribers[str(conversation_id)].add(queue)
    return queue


def unsubscribe(conversation_id, queue: asyncio.Queue):
    key = str(conversation_id)
    _subscribers[key].discard(queue)
    if not _subscribers[key]:
        del _subscribers[key]


def publish(conversation_id, event_type: str, data: Optional[Dict[str, Any]] = None):
    for queue in list(_subscribers.get(str(conversation_id), ())):
        try:
            queue.put_nowait((event_type, data or {}))
        except asyncio.QueueFull:  # a stalled subscriber misses events; the dashboard re-syncs on the next one
            pass


def publish_after_commit(session, conversation_id, event_type: str, data: Optional[Dict[str, Any]] = None):
    session.info.setdefault("live_events", []).append((str(conversation_id), event_type, data or {}))


@event.listens_for(Session, "after_commit")
def _publish_pending(session):
    for args in session.info.pop("live_events", []):
        publish(*args)


@event.listens_for(Session, "after_rollback")
def _discard_pending(session):
    session.info.pop("live_events", None)


def message_event(message: Message, author_name: Optional[str] = None) -> Dict[str, Any]:
    return {"id": str(message.id), "role": message.role.value, "content": message.content,
            "intent": message.intent, "author_name": author_name,
            "created_at": message.created_at.isoformat() if message.created_at else None}
