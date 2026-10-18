"""Masking of personal data before anything is written to logs or
shown to the model when it is not needed."""
from __future__ import annotations

import re
from typing import Any

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s\-()]{7,}\d)(?!\d)")
_UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.I)
_CN_ID = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_LONG_DIGITS = re.compile(r"(?<!\d)\d{12,19}(?!\d)")  # card and account numbers
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")
_SORT_CODE_ACCOUNT = re.compile(r"\b\d{2}-\d{2}-\d{2}\b\s*\d{8}\b")

MASK_KEYS = {"password", "password_hash", "token", "secret", "api_key", "authorization", "bank_account", "iban", "card_number"}


def mask_text(text: str | None) -> str | None:
    if not text:
        return text
    out = _EMAIL.sub("[email]", text)
    out = _CN_ID.sub("[id-number]", out)
    out = _IBAN.sub("[iban]", out)
    out = _SORT_CODE_ACCOUNT.sub("[bank-account]", out)
    out = _LONG_DIGITS.sub("[number]", out)
    out = _PHONE.sub("[phone]", out)
    out = _UK_POSTCODE.sub("[postcode]", out)
    return out


def mask_value(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "[...]"
    if isinstance(value, str):
        return mask_text(value) if len(value) < 4000 else mask_text(value[:4000]) + f"... [{len(value) - 4000} more chars]"
    if isinstance(value, dict):
        return {k: ("[redacted]" if str(k).lower() in MASK_KEYS else mask_value(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_value(v, depth + 1) for v in value[:50]]
    return value
