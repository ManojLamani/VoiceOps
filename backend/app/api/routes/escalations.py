"""Human escalation queue."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.routes.conversations import escalation_response
from app.core.privacy import for_user
from app.core.security import get_current_user, require_staff
from app.db.database import get_db
from app.db.models import Escalation
from app.db.repositories import EscalationRepository
from app.schemas.schemas import EscalationResponse, EscalationUpdate

router = APIRouter(prefix="/escalations", tags=["Escalations"])


@router.get("", response_model=list[EscalationResponse])
async def list_escalations(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                           current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return for_user(current_user,
                    [escalation_response(e) for e in await EscalationRepository(db).get_all(skip=skip, limit=limit)])


@router.patch("/{escalation_id}", response_model=EscalationResponse)
async def update_escalation(escalation_id: UUID, data: EscalationUpdate,
                            current_user: dict = Depends(require_staff), db: AsyncSession = Depends(get_db)):
    esc = (await db.execute(select(Escalation).options(selectinload(Escalation.customer))
                            .where(Escalation.id == escalation_id))).scalar_one_or_none()
    if not esc:
        raise HTTPException(status_code=404, detail="Escalation not found")
    esc.status = data.status
    if data.status == "assigned":
        esc.assigned_to = UUID(current_user["user_id"])
    esc.resolved_at = datetime.utcnow() if data.status == "resolved" else None
    await db.flush()
    return escalation_response(esc)
