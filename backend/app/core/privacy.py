"""PII masking for read-only (viewer) accounts.

Seeded staff accounts (admin/agent) see full customer records. Self-registered accounts are viewers:
they can use the product, but emails, phone numbers and addresses are masked in every response.
"""
import re
from typing import Any

PII_KEYS = {"email", "phone", "address", "shipping_address"}
_EMAIL = re.compile(r"\b([\w.+-])[\w.+-]*@([\w-]+(?:\.[\w-]+)+)\b")
# Phone-shaped numbers only (e.g. +1-555-0102, (555) 010-2000) - not dates, prices or tracking numbers.
_PHONE = re.compile(r"(?<![\w-])\+?1?[\s.-]?\(?\d{3}\)?[\s.-]\d{3,4}(?:[\s.-]\d{4})?(?!\d)")


def _mask_phone(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"***-{digits[-4:]}" if len(digits) >= 7 else match.group(0)


def redact_text(text: str) -> str:
    text = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    return _PHONE.sub(_mask_phone, text)


def mask_pii(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: mask_pii(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_pii(v, key) for v in value]
    if isinstance(value, str):
        if key in ("address", "shipping_address"):
            return "(hidden)"
        return redact_text(value)
    return value


def is_viewer(user: dict) -> bool:
    return user.get("role") not in ("admin", "agent")


def for_user(user: dict, payload: Any) -> Any:
    """Return the payload unchanged for staff, masked for viewers. Pydantic models are dumped first."""
    if not is_viewer(user):
        return payload
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    elif isinstance(payload, list):
        payload = [p.model_dump(mode="json") if hasattr(p, "model_dump") else p for p in payload]
    return mask_pii(payload)
