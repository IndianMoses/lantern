"""Insurer integration interface.

A real integration implements ``InsurerAdapter`` for one provider (REST
API, broker platform, e mail gateway). The platform never calls a provider
directly; it goes through an adapter, which makes every submission
idempotent, logged and replaceable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SubmissionResult:
    status: str  # accepted | rejected | failed
    insurer_reference: str | None
    mode: str  # integrated | assisted
    raw: dict = field(default_factory=dict)
    error: str | None = None
    is_mock: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "accepted"


@dataclass
class StatusResult:
    status: str | None
    last_updated: str | None
    raw: dict = field(default_factory=dict)
    is_mock: bool = False


class InsurerAdapter(Protocol):
    name: str

    def submit_claim(self, claim_pack: dict, *, idempotency_key: str) -> SubmissionResult: ...

    def get_claim_status(self, insurer_reference: str) -> StatusResult: ...

    def verify_webhook(self, headers: dict, body: bytes) -> bool: ...


class AdapterError(RuntimeError):
    """Raised when the provider cannot be reached or rejects the request.
    The platform records the failure and never pretends the claim was sent."""
