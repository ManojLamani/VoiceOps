"""Text normalization and entity extraction tuned for speech transcripts."""
import re
from typing import Optional

_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}
_ID_KEYWORD = r"(?:ord|order|cust|customer|tkt|ticket)"


def normalize_spoken_ids(text: str) -> str:
    """Turn spoken/spelled identifiers into canonical text.

    "O R D zero zero three" -> "ord 003", "customer number one" -> "customer number 1".
    """
    t = text
    t = re.sub(r"\bo\.?\s*r\.?\s*d\b\.?", "ord", t, flags=re.I)
    t = re.sub(r"\bc\.?\s*u\.?\s*s\.?\s*t\b\.?", "cust", t, flags=re.I)
    t = re.sub(r"\bt\.?\s*k\.?\s*t\b\.?", "tkt", t, flags=re.I)

    tokens = re.split(r"(\s+|[,.\-])", t)
    words = [tok for tok in tokens]
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w.lower() in _DIGIT_WORDS:
            run, j = [], i
            while j < len(words):
                if words[j].lower() in _DIGIT_WORDS:
                    run.append(_DIGIT_WORDS[words[j].lower()])
                    j += 1
                elif re.fullmatch(r"\s+|[,\-]", words[j] or "") and j + 1 < len(words) \
                        and words[j + 1].lower() in _DIGIT_WORDS:
                    j += 1
                else:
                    break
            prev = "".join(out).rstrip().lower()
            if len(run) >= 2 or re.search(_ID_KEYWORD + r"(?:\s*(?:number|no|id|#))?$", prev):
                out.append("".join(run))
                i = j
                continue
        out.append(w)
        i += 1
    return "".join(out)


def _extract(text: str, keyword_pattern: str, prefix: str, width: int) -> Optional[str]:
    t = normalize_spoken_ids(text)
    m = re.search(
        rf"\b{keyword_pattern}\s*(?:number|no\.?|id|#)?\s*[-#:]?\s*((?:\d[\s-]?){{1,6}})(?!\d)",
        t, flags=re.I,
    )
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return f"{prefix}{int(digits):0{width}d}" if digits else None


def extract_order_id(text: str) -> Optional[str]:
    return _extract(text, r"(?:ord|order)", "ORD", 3)


def extract_customer_id(text: str) -> Optional[str]:
    return _extract(text, r"(?:cust|customer)", "CUST", 3)


def extract_ticket_id(text: str) -> Optional[str]:
    return _extract(text, r"(?:tkt|ticket)", "TKT", 4)


def extract_email(text: str) -> Optional[str]:
    t = re.sub(r"\s+at\s+", "@", text, flags=re.I)
    t = re.sub(r"\s+dot\s+", ".", t, flags=re.I)
    m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", t)
    return m.group(0).lower().rstrip(".") if m else None


def extract_phone(text: str) -> Optional[str]:
    m = re.search(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{4}\b|\+?1?[\s.-]?\d{3}[\s.-]\d{3}[\s.-]\d{4}", text)
    return re.sub(r"\D", "", m.group(0)) if m else None


def normalize_identifier(value, prefix: str, width: int) -> str:
    """Canonicalize an ID passed by the LLM ("ord-3", "order 003", "ORD003") -> "ORD003"."""
    raw = normalize_spoken_ids(str(value or "")).strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return raw.upper()
    return f"{prefix}{int(digits):0{width}d}"


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[-10:] if len(digits) >= 10 else digits


_AFFIRM = re.compile(
    r"^\s*(?:yes|yeah|yep|yup|sure|ok(?:ay)?|correct|right|confirm(?:ed)?|absolutely|definitely|"
    r"please do|go ahead|do it|that'?s right|that is right|that'?s correct|affirmative|of course|"
    r"yes please|please proceed|proceed)\b",
    re.I,
)
_AFFIRM_ANYWHERE = re.compile(r"\b(?:yes,? (?:please )?(?:cancel|go ahead|do it|proceed)|i confirm|go ahead and)\b", re.I)
_DENY = re.compile(
    r"^\s*(?:no|nope|nah|don'?t|do not|never ?mind|stop|wait|hold on|not now|cancel that|keep it|"
    r"i changed my mind|i'?ve changed my mind|leave it)\b",
    re.I,
)


def classify_confirmation(text: str) -> Optional[bool]:
    """Fast-path yes/no detection for a pending confirmation. None = unclear."""
    t = text.strip().lower()
    if _DENY.search(t) or re.search(r"\b(?:don'?t|do not) (?:cancel|return|do|change|update)\b", t):
        return False
    if _AFFIRM.search(t) or _AFFIRM_ANYWHERE.search(t):
        return True
    return None
