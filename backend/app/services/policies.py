"""Business rules enforced by the backend, independent of the LLM.

Mirrors data/knowledge_base/cancellation_policy.md, return_policy.md and refund_policy.md.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app.db.models import Order, OrderStatus

RETURN_WINDOW_DAYS = 30
CANCELLABLE_STATUSES = {OrderStatus.PENDING, OrderStatus.CONFIRMED}
RETURN_SHIPPING_FEE = 7.99


@dataclass
class Eligibility:
    eligible: bool
    reason: str


def status_of(order: Order) -> OrderStatus:
    return order.status if isinstance(order.status, OrderStatus) else OrderStatus(order.status)


def cancellation_eligibility(order: Order) -> Eligibility:
    status = status_of(order)
    if status == OrderStatus.CANCELLED:
        return Eligibility(False, "The order is already cancelled.")
    if status not in CANCELLABLE_STATUSES:
        hint = " Once it's delivered, you can return it instead." \
            if status in (OrderStatus.SHIPPED, OrderStatus.IN_TRANSIT) else \
            (" Delivered orders can be returned instead." if status == OrderStatus.DELIVERED else "")
        return Eligibility(False, f"It's already {status.value.replace('_', ' ')}, and only orders that haven't "
                                  f"entered processing can be cancelled.{hint}")
    if not order.cancellation_eligible:
        return Eligibility(False, "This order is marked as non-cancellable (e.g. a custom or personalized order).")
    return Eligibility(True, "Order has not entered processing and can be cancelled for a full refund.")


def return_deadline(order: Order) -> Optional[datetime]:
    if order.return_deadline:
        return order.return_deadline
    if order.actual_delivery:
        return order.actual_delivery + timedelta(days=RETURN_WINDOW_DAYS)
    return None


def return_eligibility(order: Order, now: Optional[datetime] = None) -> Eligibility:
    now = now or datetime.utcnow()
    status = status_of(order)
    if status in (OrderStatus.RETURN_REQUESTED, OrderStatus.RETURNED, OrderStatus.REFUNDED):
        return Eligibility(False, f"A return has already been processed for this order (status '{status.value}').")
    if status != OrderStatus.DELIVERED:
        return Eligibility(False, f"Returns are only possible after delivery, and this order is "
                                  f"{status.value.replace('_', ' ')}.")
    deadline = return_deadline(order)
    if deadline and now > deadline:
        return Eligibility(False, f"The 30-day return window ended on {deadline.date().isoformat()}.")
    if not order.return_eligible:
        return Eligibility(False, "The items in this order are marked non-returnable (e.g. final sale).")
    return Eligibility(True, f"Delivered and within the return window (until {deadline.date().isoformat()})."
                       if deadline else "Delivered and within the return window.")
