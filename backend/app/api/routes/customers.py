"""Customer management routes."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.database import get_db
from app.db.repositories import CustomerRepository, OrderRepository
from app.core.privacy import for_user
from app.core.security import get_current_user, require_staff
from app.schemas.schemas import CustomerResponse, CustomerUpdate, OrderResponse

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.get("", response_model=list[CustomerResponse])
async def list_customers(skip: int = Query(0, ge=0),
                          limit: int = Query(100, ge=1, le=200),
                          current_user: dict = Depends(get_current_user),
                          db: AsyncSession = Depends(get_db)):
    """List all customers."""
    repo = CustomerRepository(db)
    customers = await repo.get_all(skip=skip, limit=limit)
    return for_user(current_user, [CustomerResponse.model_validate(c) for c in customers])


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer(customer_id: str,
                       current_user: dict = Depends(get_current_user),
                       db: AsyncSession = Depends(get_db)):
    """Get a customer by customer_id (e.g., CUST001)."""
    repo = CustomerRepository(db)
    customer = await repo.get_by_customer_id(customer_id.upper())
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return for_user(current_user, CustomerResponse.model_validate(customer))


@router.put("/{customer_id}", response_model=CustomerResponse)
async def update_customer(customer_id: str, data: CustomerUpdate,
                           current_user: dict = Depends(require_staff),
                           db: AsyncSession = Depends(get_db)):
    """Update customer information."""
    repo = CustomerRepository(db)
    customer = await repo.get_by_customer_id(customer_id.upper())
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    update_data = data.model_dump(exclude_none=True)
    if "email" in update_data:
        update_data["email"] = update_data["email"].lower()
        other = await repo.get_by_email(update_data["email"])
        if other and other.id != customer.id:
            raise HTTPException(status_code=409, detail="That email is already used by another customer")
    if update_data:
        customer = await repo.update(customer, **update_data)
    return CustomerResponse.model_validate(customer)


@router.get("/{customer_id}/orders", response_model=list[OrderResponse])
async def get_customer_orders(customer_id: str,
                               current_user: dict = Depends(get_current_user),
                               db: AsyncSession = Depends(get_db)):
    """Get all orders for a customer."""
    cust_repo = CustomerRepository(db)
    customer = await cust_repo.get_by_customer_id(customer_id.upper())
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    from app.api.routes.orders import order_response
    orders = await OrderRepository(db).get_by_customer(customer.id)
    for o in orders:
        o.customer = customer
    return for_user(current_user, [order_response(o) for o in orders])
