"""Ticket management routes."""
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.db.repositories import TicketRepository, CustomerRepository, UserRepository
from app.core.privacy import for_user
from app.core.security import get_current_user, require_staff
from app.db.models import TicketStatus, TicketPriority
from app.schemas.schemas import TicketCreate, TicketUpdate, TicketResponse

router = APIRouter(prefix="/tickets", tags=["Tickets"])


@router.get("", response_model=list[TicketResponse])
async def list_tickets(skip: int = Query(0, ge=0),
                        limit: int = Query(50, ge=1, le=100),
                        current_user: dict = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    """List all support tickets."""
    repo = TicketRepository(db)
    tickets = await repo.get_all(skip=skip, limit=limit)
    return for_user(current_user, [TicketResponse.model_validate(t) for t in tickets])


@router.post("", response_model=TicketResponse, status_code=201)
async def create_ticket(data: TicketCreate,
                         current_user: dict = Depends(require_staff),
                         db: AsyncSession = Depends(get_db)):
    """Create a new support ticket."""
    cust_repo = CustomerRepository(db)
    customer = await cust_repo.get_by_customer_id(data.customer_id.upper())
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    ticket_repo = TicketRepository(db)
    ticket_id = await ticket_repo.next_ticket_id()
    ticket = await ticket_repo.create(
        ticket_id=ticket_id,
        customer_id=customer.id,
        subject=data.subject,
        description=data.description,
        category=data.category,
        priority=data.priority or "medium",
    )
    return TicketResponse.model_validate(ticket)


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(ticket_id: str,
                     current_user: dict = Depends(get_current_user),
                     db: AsyncSession = Depends(get_db)):
    """Get ticket by ticket_id (e.g., TKT0001)."""
    repo = TicketRepository(db)
    ticket = await repo.get_by_ticket_id(ticket_id.upper())
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return TicketResponse.model_validate(ticket)


@router.put("/{ticket_id}", response_model=TicketResponse)
async def update_ticket(ticket_id: str, data: TicketUpdate,
                         current_user: dict = Depends(require_staff),
                         db: AsyncSession = Depends(get_db)):
    """Update a support ticket."""
    repo = TicketRepository(db)
    ticket = await repo.get_by_ticket_id(ticket_id.upper())
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    update_data = data.model_dump(exclude_none=True)
    if "assigned_to" in update_data and not await UserRepository(db).get_by_id(update_data["assigned_to"]):
        raise HTTPException(status_code=422, detail="assigned_to must be an existing staff user")
    try:
        if "status" in update_data:
            update_data["status"] = TicketStatus(update_data["status"])
            if update_data["status"] in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
                update_data["resolved_at"] = datetime.utcnow()
        if "priority" in update_data:
            update_data["priority"] = TicketPriority(update_data["priority"])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if update_data:
        ticket = await repo.update(ticket, **update_data)
    return TicketResponse.model_validate(ticket)
