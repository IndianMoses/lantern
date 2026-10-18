"""Mock insurer adapter used for development, tests and the sandbox demo.

It behaves like a provider API would: it is idempotent on the key, returns a
reference, and can be made to fail deterministically (any claim whose
description contains ``SIMULATE_ADAPTER_FAILURE``) so the failure path can be
demonstrated and tested. Every response is marked ``is_mock=True`` and the
interface shows it as a mock acknowledgement, never as a verified one.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading

from ...config import settings
from .base import AdapterError, StatusResult, SubmissionResult


class MockInsurerAdapter:
    name = "mock_insurer"

    def __init__(self, org_code: str = "MOCK", webhook_secret: str | None = None) -> None:
        self.org_code = org_code
        self._seen: dict[str, SubmissionResult] = {}
        self._lock = threading.Lock()
        self._webhook_secret = (webhook_secret or settings.secret_key).encode("utf-8")

    def submit_claim(self, claim_pack: dict, *, idempotency_key: str) -> SubmissionResult:
        with self._lock:
            if idempotency_key in self._seen:
                prior = self._seen[idempotency_key]
                return SubmissionResult(status=prior.status, insurer_reference=prior.insurer_reference, mode="integrated", raw={**prior.raw, "idempotent_replay": True}, is_mock=True)
            description = str(claim_pack.get("incident", {}).get("description", ""))
            if settings.mock_adapter_fail_keyword in description:
                raise AdapterError("mock provider returned HTTP 503 Service Unavailable")
            missing = [f for f in ("claim_reference", "policy_number", "incident") if f not in claim_pack]
            if missing:
                result = SubmissionResult(status="rejected", insurer_reference=None, mode="integrated", raw={"missing": missing}, error="missing required fields", is_mock=True)
            else:
                prefix = "".join(ch for ch in self.org_code.upper() if ch.isalnum())[:4] or "MOCK"
                ref = f"{prefix}-ACK-{secrets.randbelow(90000) + 10000}"
                result = SubmissionResult(status="accepted", insurer_reference=ref, mode="integrated", raw={"message": "Claim received by mock provider", "received_documents": len(claim_pack.get("documents", [])), "mock": True}, is_mock=True)
            self._seen[idempotency_key] = result
            return result

    def get_claim_status(self, insurer_reference: str) -> StatusResult:
        return StatusResult(status=None, last_updated=None, raw={"message": "mock provider does not push status; insurer portal updates status"}, is_mock=True)

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        sig = headers.get("x-mock-signature", "")
        expected = hmac.new(self._webhook_secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)


_adapters: dict[str, MockInsurerAdapter] = {}


def get_adapter(org_code: str | None, integration_mode: str = "mock") -> MockInsurerAdapter:
    """Factory. Real adapters would be selected here by organisation."""
    key = org_code or "MOCK"
    if key not in _adapters:
        _adapters[key] = MockInsurerAdapter(org_code=key)
    return _adapters[key]
