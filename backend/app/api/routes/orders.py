"""Order management routes (staff dashboard). Business rules are shared with the agent tools."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.privacy import for_user
from app.core.security import get_current_user, require_staff
from app.db.database import get_db
from app.db.models import Order, OrderStatus
from app.db.repositories import OrderRepository
from app.schemas.schemas import OrderResponse, OrderCancelRequest, ReturnRequest
from app.services import policies

router = APIRouter(prefix="/orders", tags=["Orders"])


def order_response(o: Order) -> OrderResponse:
    data = OrderResponse.model_validate(o)
    if o.customer:
        data.customer_code, data.customer_name = o.customer.customer_id, o.customer.name
    data.can_cancel = policies.cancellation_eligibility(o).eligible
    data.can_return = policies.return_eligibility(o).eligible
    return data


async def _load(db: AsyncSession, order_id: str) -> Order:
    order = (await db.execute(select(Order).options(selectinload(Order.customer))
                              .where(Order.order_id == order_id.upper()))).scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.get("", response_model=list[OrderResponse])
async def list_orders(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
                      status: Optional[str] = None, current_user: dict = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    query = select(Order).options(selectinload(Order.customer)).order_by(Order.order_date.desc())
    if status:
        try:
            query = query.where(Order.status == OrderStatus(status))
        except ValueError:
            raise HTTPException(status_code=422, detail="Unknown order status")
    orders = (await db.execute(query.offset(skip).limit(limit))).scalars().all()
    return for_user(current_user, [order_response(o) for o in orders])


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(order_id: str, current_user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return for_user(current_user, order_response(await _load(db, order_id)))


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(order_id: str, data: Optional[OrderCancelRequest] = None,
                       current_user: dict = Depends(require_staff), db: AsyncSession = Depends(get_db)):
    order = await _load(db, order_id)
    elig = policies.cancellation_eligibility(order)
    if not elig.eligible:
        raise HTTPException(status_code=400, detail=elig.reason)
    await OrderRepository(db).update_status(
        order, OrderStatus.CANCELLED, cancellation_eligible=False, payment_status="refund_pending",
        refund_amount=order.total_amount,
        notes=f"Cancelled by staff ({current_user['email']}) on {datetime.utcnow():%Y-%m-%d}. "
              f"Reason: {(data.reason if data else None) or 'not given'}")
    return order_response(order)


@router.post("/{order_id}/return", response_model=OrderResponse)
async def create_return(order_id: str, data: ReturnRequest, current_user: dict = Depends(require_staff),
                        db: AsyncSession = Depends(get_db)):
    order = await _load(db, order_id)
    elig = policies.return_eligibility(order)
    if not elig.eligible:
        raise HTTPException(status_code=400, detail=elig.reason)
    await OrderRepository(db).update_status(
        order, OrderStatus.RETURN_REQUESTED, return_eligible=False,
        notes=f"Return RET-{order.order_id} created by staff ({current_user['email']}). Reason: {data.reason}")
    return order_response(order)
