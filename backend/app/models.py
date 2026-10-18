"""Relational data model.

Public identifiers are UUID4 strings so that records cannot be enumerated.
Tenant isolation: every policy and claim carries the insurer organisation
that administers it (``insurer_org_id``) and the customer that owns it
(``user_id``). Server side queries always filter on one of the two.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------- identity ---


class Organization(TimestampMixin, Base):
    """An insurer tenant. Staff users belong to exactly one organisation."""

    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_zh: Mapped[str | None] = mapped_column(String(120))
    claims_email: Mapped[str | None] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2), default="GB")
    integration_mode: Mapped[str] = mapped_column(String(20), default="mock")  # mock | api | assisted

    staff: Mapped[list[User]] = relationship(back_populates="organization")


class Roles:
    CUSTOMER = "customer"
    INSURER_HANDLER = "insurer_handler"
    INSURER_ADMIN = "insurer_admin"
    PLATFORM_ADMIN = "platform_admin"
    INSURER_ROLES = {INSURER_HANDLER, INSURER_ADMIN}


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False, default=Roles.CUSTOMER)
    org_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    locale: Mapped[str] = mapped_column(String(8), default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    organization: Mapped[Organization | None] = relationship(back_populates="staff")
    profile: Mapped[CustomerProfile | None] = relationship(back_populates="user", uselist=False)

    @property
    def is_insurer(self) -> bool:
        return self.role in Roles.INSURER_ROLES


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # random token hash
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ip: Mapped[str | None] = mapped_column(String(64))


class CustomerProfile(TimestampMixin, Base):
    """Only what the three workflows need. No special category data."""

    __tablename__ = "customer_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    country: Mapped[str] = mapped_column(String(2), default="GB")
    city: Mapped[str | None] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    housing: Mapped[str | None] = mapped_column(String(20))  # owner | renter | living_with_family
    household: Mapped[dict] = mapped_column(JSON, default=dict)  # adults, children, dependants
    assets: Mapped[list] = mapped_column(JSON, default=list)  # [{type, label, value, notes}]
    activities: Mapped[list] = mapped_column(JSON, default=list)  # travel, work_from_home, delivery_rider ...
    concerns: Mapped[list] = mapped_column(JSON, default=list)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)  # notifications, language
    consents: Mapped[dict] = mapped_column(JSON, default=dict)

    user: Mapped[User] = relationship(back_populates="profile")


# ---------------------------------------------------------------- policies ---


class PolicyStatus:
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    PENDING_REVIEW = "pending_review"  # uploaded, extraction not yet confirmed


class Policy(TimestampMixin, Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    insurer_org_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    provider_name: Mapped[str] = mapped_column(String(120), nullable=False)
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    insurance_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    policy_number: Mapped[str] = mapped_column(String(60), nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="en")
    country: Mapped[str] = mapped_column(String(2), default="GB")
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    premium_amount: Mapped[float | None] = mapped_column(Float)
    premium_frequency: Mapped[str | None] = mapped_column(String(20))  # annual | monthly
    excess_amount: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default=PolicyStatus.ACTIVE, index=True)
    source: Mapped[str] = mapped_column(String(20), default="seeded")  # seeded | uploaded
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id"))
    insured_items: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)  # key limits etc. for cards
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    is_demo_data: Mapped[bool] = mapped_column(Boolean, default=True)

    clauses: Mapped[list[PolicyClause]] = relationship(back_populates="policy", cascade="all, delete-orphan", order_by="PolicyClause.position")


class ClauseKind:
    COVERAGE = "coverage"
    EXCLUSION = "exclusion"
    CONDITION = "condition"
    LIMIT = "limit"
    DEFINITION = "definition"
    CLAIMS_PROCEDURE = "claims_procedure"
    GENERAL = "general"


class PolicyClause(Base):
    __tablename__ = "policy_clauses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id"), index=True, nullable=False)
    clause_ref: Mapped[str] = mapped_column(String(30), nullable=False)  # e.g. "4.2"
    section: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="en")
    kind: Mapped[str] = mapped_column(String(30), default=ClauseKind.GENERAL, index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    amount: Mapped[float | None] = mapped_column(Float)  # a limit, sub limit or excess if the clause states one
    position: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    translations: Mapped[dict] = mapped_column(JSON, default=dict)  # {"en": "...", "zh": "..."} when available

    policy: Mapped[Policy] = relationship(back_populates="clauses")

    @property
    def citation(self) -> str:
        return f"{self.policy.policy_number} §{self.clause_ref}"


# --------------------------------------------------------------- documents ---


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    uploaded_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id"), index=True)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(40), default="other")  # policy_wording | receipt | photo | estimate | police_report | correspondence | other
    checklist_item_key: Mapped[str | None] = mapped_column(String(60))
    extraction_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | done | failed | not_applicable
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extraction_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    injection_flags: Mapped[list] = mapped_column(JSON, default=list)
    reviewed_by_insurer: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_demo_data: Mapped[bool] = mapped_column(Boolean, default=False)


# ------------------------------------------------------------------ claims ---


class ClaimStatus:
    DRAFT = "draft"
    INFORMATION_REQUIRED = "information_required"
    READY_FOR_REVIEW = "ready_for_review"
    AWAITING_CUSTOMER_APPROVAL = "awaiting_customer_approval"
    SUBMITTED = "submitted"
    ACKNOWLEDGED = "acknowledged"
    UNDER_REVIEW = "under_review"
    ADDITIONAL_INFORMATION_REQUESTED = "additional_information_requested"
    DECISION_ISSUED = "decision_issued"
    APPROVED = "approved"
    PARTIALLY_APPROVED = "partially_approved"
    DECLINED = "declined"
    SETTLED = "settled"
    CLOSED = "closed"
    REOPENED = "reopened"
    SUBMISSION_FAILED = "submission_failed"

    PRE_SUBMISSION = {DRAFT, INFORMATION_REQUIRED, READY_FOR_REVIEW, AWAITING_CUSTOMER_APPROVAL, SUBMISSION_FAILED}
    OPEN_WITH_INSURER = {SUBMITTED, ACKNOWLEDGED, UNDER_REVIEW, ADDITIONAL_INFORMATION_REQUESTED, REOPENED}
    DECIDED = {APPROVED, PARTIALLY_APPROVED, DECLINED, DECISION_ISSUED}
    TERMINAL = {SETTLED, CLOSED}


class Claim(TimestampMixin, Base):
    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    reference: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id"), index=True)
    insurer_org_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    insurance_type: Mapped[str] = mapped_column(String(40), nullable=False)
    incident_type: Mapped[str] = mapped_column(String(40), nullable=False)
    incident_date: Mapped[date | None] = mapped_column(Date)
    incident_time: Mapped[str | None] = mapped_column(String(10))
    location: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    items_involved: Mapped[list] = mapped_column(JSON, default=list)
    estimated_loss: Mapped[float | None] = mapped_column(Float)
    loss_unknown: Mapped[bool] = mapped_column(Boolean, default=False)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    third_party_involved: Mapped[bool | None] = mapped_column(Boolean)
    authorities_involved: Mapped[bool | None] = mapped_column(Boolean)
    authority_reference: Mapped[str | None] = mapped_column(String(80))
    actions_taken: Mapped[str | None] = mapped_column(Text)
    safety_concern: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str] = mapped_column(String(8), default="en")
    status: Mapped[str] = mapped_column(String(40), default=ClaimStatus.DRAFT, index=True)
    status_source: Mapped[str] = mapped_column(String(20), default="platform")  # platform | insurer | adapter
    status_updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    priority: Mapped[str] = mapped_column(String(10), default="normal")
    assigned_handler_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    checklist: Mapped[list] = mapped_column(JSON, default=list)
    completeness: Mapped[dict] = mapped_column(JSON, default=dict)
    coverage_view: Mapped[dict] = mapped_column(JSON, default=dict)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime)
    insurer_reference: Mapped[str | None] = mapped_column(String(60))
    is_demo_data: Mapped[bool] = mapped_column(Boolean, default=False)


class ClaimEvent(Base):
    __tablename__ = "claim_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), index=True, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)  # customer | insurer | agent | system | adapter
    actor_id: Mapped[str | None] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    visible_to_customer: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class InformationRequest(TimestampMixin, Base):
    __tablename__ = "information_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), index=True, nullable=False)
    requested_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    requested_items: Mapped[list] = mapped_column(JSON, default=list)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | answered | withdrawn
    response_text: Mapped[str | None] = mapped_column(Text)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime)


class Communication(TimestampMixin, Base):
    """Drafted and sent messages between a customer and an insurer."""

    __tablename__ = "communications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id"), index=True)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    insurer_org_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # outbound (customer -> insurer) | inbound
    channel: Mapped[str] = mapped_column(String(20), default="in_app")  # in_app | email_draft
    kind: Mapped[str] = mapped_column(String(40), default="message")
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    recipient: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved | sent | received
    generated_by: Mapped[str] = mapped_column(String(20), default="user")  # user | agent | insurer_staff
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approval_requests.id"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    sender_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    referenced_document_ids: Mapped[list] = mapped_column(JSON, default=list)


class ClaimDecision(Base):
    __tablename__ = "claim_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), index=True, nullable=False)
    decided_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), nullable=False)  # approved | partially_approved | declined
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    clause_refs: Mapped[list] = mapped_column(JSON, default=list)
    approved_amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Submission(Base):
    """Record of every attempt to send a claim to an insurer channel."""

    __tablename__ = "submissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), index=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    adapter: Mapped[str] = mapped_column(String(40), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # integrated | assisted
    request_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    response_payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # succeeded | failed | duplicate
    error: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approval_requests.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ------------------------------------------------------------------- agent ---


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # current workflow state, claim id etc.
    surface: Mapped[str] = mapped_column(String(20), default="customer")  # customer | insurer_copilot


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant | system
    content: Mapped[str] = mapped_column(Text, default="")
    blocks: Mapped[list] = mapped_column(JSON, default=list)  # structured UI blocks (citations, checklist, approval card ...)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    language: Mapped[str] = mapped_column(String(8), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), index=True)
    intent: Mapped[str] = mapped_column(String(40), default="general")
    workflow: Mapped[str] = mapped_column(String(40), default="general")
    plan: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | completed | failed | awaiting_approval
    provider: Mapped[str] = mapped_column(String(30), default="deterministic")
    model: Mapped[str | None] = mapped_column(String(60))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    grounding: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)


class ToolExecution(Base):
    __tablename__ = "agent_tool_executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True, nullable=False)
    step: Mapped[int] = mapped_column(Integer, default=0)
    tool_name: Mapped[str] = mapped_column(String(60), nullable=False)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    input_masked: Mapped[dict] = mapped_column(JSON, default=dict)
    output_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # ok | denied | approval_required | error
    error: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approval_requests.id"))
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ApprovalRequest(Base):
    """A pending consequential action. Executed only by backend services
    after the owning user approves it from the interface."""

    __tablename__ = "approval_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    action_type: Mapped[str] = mapped_column(String(40), nullable=False)  # submit_claim | send_communication | respond_information_request
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    consequences: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected | expired | consumed | failed
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class GapAnalysis(Base):
    __tablename__ = "gap_analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    findings: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    inputs_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(200))
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuditEvent(Base):
    """Append only, hash chained security audit log. Never edited by users."""

    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("seq", name="uq_audit_seq"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(36))
    outcome: Mapped[str] = mapped_column(String(20), default="success")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
