"""Bank integration layer and consent.

A bank integrates Lantern without replacing its own systems through eight
provider agnostic interfaces. Every one of them ships here as a *mock* that
is labelled as such in every response (``"mock": true``) and records what it
was asked to do so the integration page can show health. No bank API is
invented and no live connection is claimed: ``integration_health`` reports
``mock`` or ``not_configured`` until a real adapter is registered.

Data boundary: the platform never ingests transaction data, balances or credit
data. The profile interface returns only the fields the insurance workflows
use, and only for purposes the customer consented to.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ConsentRecord, Organization, User, utcnow

# ---------------------------------------------------------------- consent ---

PURPOSES: dict[str, dict] = {
    "insurance_workflows": {"en": "Use my profile and policies for policy questions, gap analysis and claims", "zh": "将我的资料和保单用于保单问答、缺口分析和理赔", "default": True, "locked": True},
    "share_with_insurers_on_submission": {"en": "Share the claim pack with the insurer only when I approve a submission", "zh": "仅在我批准提交时向保险公司发送理赔材料包", "default": True, "locked": True},
    "analytics_aggregate": {"en": "Count me in anonymous aggregate statistics shown to my bank", "zh": "将我计入向银行展示的匿名汇总统计", "default": False, "locked": False},
    "bank_profile_import": {"en": "Let my bank pre fill my profile from what it already holds (name, address, products)", "zh": "允许银行用已有信息（姓名、地址、产品）预填我的资料", "default": False, "locked": False},
    "bank_communications": {"en": "Receive insurance messages through my bank's approved channels", "zh": "通过银行认可的渠道接收保险相关消息", "default": False, "locked": False},
    "external_model_processing": {"en": "Allow an external AI provider to process my questions and clause excerpts (otherwise only platform run models are used)", "zh": "允许外部 AI 服务处理我的问题和条款摘录（否则仅使用平台内运行的模型）", "default": True, "locked": False},
    "document_retention_extended": {"en": "Keep my uploaded documents after a claim closes (otherwise they are scheduled for deletion)", "zh": "理赔结束后保留我上传的文件（否则将安排删除）", "default": False, "locked": False},
}


def current_consents(db: Session, user_id: str) -> dict[str, dict]:
    rows = db.execute(select(ConsentRecord).where(ConsentRecord.user_id == user_id).order_by(ConsentRecord.created_at.asc())).scalars().all()
    latest: dict[str, ConsentRecord] = {}
    for r in rows:
        latest[r.purpose] = r
    out = {}
    for purpose, meta in PURPOSES.items():
        r = latest.get(purpose)
        out[purpose] = {"purpose": purpose, "label": {"en": meta["en"], "zh": meta["zh"]}, "granted": r.granted if r else meta["default"], "source": r.source if r else "default", "recorded_at": r.created_at.isoformat() if r else None, "locked": meta["locked"]}
    return out


def allowed(db: Session, user_id: str, purpose: str) -> bool:
    return current_consents(db, user_id).get(purpose, {}).get("granted", False)


def record_consent(db: Session, user_id: str, purpose: str, granted: bool, *, source: str = "customer", note: str | None = None) -> ConsentRecord:
    if purpose not in PURPOSES:
        raise ValueError("unknown purpose")
    if PURPOSES[purpose]["locked"] and not granted:
        raise ValueError("this purpose is required for the service to work; close the account to withdraw it")
    row = ConsentRecord(user_id=user_id, purpose=purpose, granted=granted, source=source, note=note)
    db.add(row)
    db.flush()
    return row


# ------------------------------------------------------------- interfaces ---


@dataclass
class ExternalIdentity:
    subject: str
    email: str | None
    display_name: str | None
    assurance_level: str  # e.g. "bank_sso"
    mock: bool = True


@dataclass
class ProfileImport:
    fields: dict  # only: full_name, country, city, currency, products (insurance-relevant) — never balances
    source: str
    mock: bool = True


@dataclass
class Delivery:
    channel: str
    reference: str
    accepted: bool
    mock: bool = True
    detail: str = ""


class IdentityProvider(Protocol):
    def verify_assertion(self, assertion: str) -> ExternalIdentity: ...


class CustomerProfileSource(Protocol):
    def fetch_profile(self, customer_ref: str, *, purpose: str) -> ProfileImport: ...


class ConsentManager(Protocol):
    def sync(self, customer_ref: str, consents: dict[str, bool]) -> bool: ...


class DocumentSource(Protocol):
    def list_documents(self, customer_ref: str) -> list[dict]: ...


class CommunicationsChannel(Protocol):
    def send(self, customer_ref: str, subject: str, body: str) -> Delivery: ...


class AuditSink(Protocol):
    def deliver(self, events: list[dict]) -> int: ...


class AnalyticsSink(Protocol):
    def publish(self, metrics: dict) -> bool: ...


class EmbeddingContext(Protocol):
    def deep_link(self, path: str, customer_ref: str) -> str: ...


# ----------------------------------------------------------------- mocks ---


@dataclass
class MockAdapters:
    """In memory stand ins. Every result says mock=True; nothing leaves the process."""

    org: Organization | None
    calls: list[dict] = field(default_factory=list)

    def _log(self, iface: str, op: str, **kw) -> None:
        self.calls.append({"interface": iface, "operation": op, "at": utcnow().isoformat(), **kw})

    # IdentityProvider
    def verify_assertion(self, assertion: str) -> ExternalIdentity:
        self._log("identity", "verify_assertion")
        # a signed assertion from the bank's identity provider would be verified here; the mock accepts a fixed shape
        if not assertion.startswith("mock-sso:"):
            raise ValueError("assertion not issued by the mock identity provider")
        subject = assertion.split(":", 1)[1]
        return ExternalIdentity(subject=subject, email=None, display_name=None, assurance_level="bank_sso", mock=True)

    # CustomerProfileSource
    def fetch_profile(self, customer_ref: str, *, purpose: str) -> ProfileImport:
        self._log("profile", "fetch_profile", purpose=purpose)
        return ProfileImport(fields={"country": "GB", "currency": "GBP", "products": ["current_account", "packaged_account_travel_insurance"]}, source="mock_bank_crm", mock=True)

    # ConsentManager
    def sync(self, customer_ref: str, consents: dict[str, bool]) -> bool:
        self._log("consent", "sync", purposes=sorted(consents))
        return True

    # DocumentSource
    def list_documents(self, customer_ref: str) -> list[dict]:
        self._log("documents", "list_documents")
        return []

    # CommunicationsChannel
    def send(self, customer_ref: str, subject: str, body: str) -> Delivery:
        self._log("communications", "send", subject=subject[:80])
        return Delivery(channel="mock_bank_secure_messaging", reference=f"MOCK-MSG-{int(time.time())}", accepted=True, mock=True, detail="Not delivered anywhere; recorded for the demonstration only.")

    # AuditSink
    def deliver(self, events: list[dict]) -> int:
        self._log("audit", "deliver", count=len(events))
        return len(events)

    # AnalyticsSink
    def publish(self, metrics: dict) -> bool:
        self._log("analytics", "publish", keys=sorted(metrics))
        return True

    # EmbeddingContext
    def deep_link(self, path: str, customer_ref: str) -> str:
        self._log("embedding", "deep_link", path=path)
        sig = hmac.new(settings.secret_key.encode(), f"{path}|{customer_ref}".encode(), hashlib.sha256).hexdigest()[:16]
        return f"/{path.lstrip('/')}?embed=mock&sig={sig}"


_registry: dict[str, MockAdapters] = {}


def adapters_for(org: Organization | None) -> MockAdapters:
    key = org.id if org else "default"
    if key not in _registry:
        _registry[key] = MockAdapters(org=org)
    return _registry[key]


INTERFACES = [
    ("identity", "Identity and access management (SSO)", "IdentityProvider.verify_assertion"),
    ("profile", "Customer profile retrieval", "CustomerProfileSource.fetch_profile"),
    ("consent", "Consent management", "ConsentManager.sync"),
    ("documents", "Document retrieval", "DocumentSource.list_documents"),
    ("communications", "Customer communications", "CommunicationsChannel.send"),
    ("audit", "Audit event delivery", "AuditSink.deliver"),
    ("analytics", "Analytics and reporting", "AnalyticsSink.publish"),
    ("embedding", "Banking app embedding / deep links", "EmbeddingContext.deep_link"),
]


def integration_health(org: Organization | None) -> list[dict]:
    a = adapters_for(org)
    out = []
    for key, label, method in INTERFACES:
        calls = [c for c in a.calls if c["interface"] == key]
        out.append({"interface": key, "label": label, "method": method, "mode": "mock", "status": "mock", "calls": len(calls), "last_call_at": calls[-1]["at"] if calls else None, "note": "Mock adapter. No live connection exists."})
    return out


def consent_statistics(db: Session, user_ids: list[str]) -> dict[str, dict]:
    """Aggregate only: how many of the bank's customers granted each purpose."""
    stats = {p: {"granted": 0, "declined": 0, "default": 0} for p in PURPOSES}
    for uid in user_ids:
        for purpose, c in current_consents(db, uid).items():
            stats[purpose]["granted" if c["granted"] else "declined"] += 1
            if c["source"] == "default":
                stats[purpose]["default"] += 1
    return stats


def ensure_default_consents(db: Session, user: User) -> None:
    """Record explicit default rows at onboarding so the audit trail shows what applied."""
    existing = {r.purpose for r in db.execute(select(ConsentRecord).where(ConsentRecord.user_id == user.id)).scalars()}
    for purpose, meta in PURPOSES.items():
        if purpose not in existing:
            db.add(ConsentRecord(user_id=user.id, purpose=purpose, granted=bool(meta["default"]), source="default"))
    db.flush()


__all__ = ["PURPOSES", "current_consents", "allowed", "record_consent", "adapters_for", "integration_health", "consent_statistics", "ensure_default_consents", "MockAdapters", "datetime"]
