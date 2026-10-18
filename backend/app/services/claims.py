"""Claim lifecycle: drafts, analysis, approval gated submission, insurer
actions and the activity timeline. Everything that changes a claim goes
through here so that status transitions are validated and every change
leaves a claim event and, where relevant, an audit record."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..config import settings
from ..models import (
    ApprovalRequest,
    Claim,
    ClaimDecision,
    ClaimEvent,
    ClaimStatus,
    Communication,
    Document,
    InformationRequest,
    Notification,
    Organization,
    Policy,
    PolicyClause,
    Roles,
    Submission,
    User,
    utcnow,
)
from ..security import new_approval_token, payload_hash
from . import checklist as checklist_svc
from . import claim_pack as pack_svc
from . import completeness as completeness_svc
from . import coverage as coverage_svc
from .adapters import AdapterError, get_adapter


class TransitionError(ValueError):
    pass


# who may move a claim from -> to
TRANSITIONS: dict[str, dict[str, set[str]]] = {
    ClaimStatus.DRAFT: {ClaimStatus.INFORMATION_REQUIRED: {"system"}, ClaimStatus.READY_FOR_REVIEW: {"system"}},
    ClaimStatus.INFORMATION_REQUIRED: {ClaimStatus.READY_FOR_REVIEW: {"system"}, ClaimStatus.DRAFT: {"system"}},
    ClaimStatus.READY_FOR_REVIEW: {ClaimStatus.AWAITING_CUSTOMER_APPROVAL: {"customer", "agent"}, ClaimStatus.INFORMATION_REQUIRED: {"system"}},
    ClaimStatus.AWAITING_CUSTOMER_APPROVAL: {ClaimStatus.SUBMITTED: {"system"}, ClaimStatus.SUBMISSION_FAILED: {"system"}, ClaimStatus.READY_FOR_REVIEW: {"customer", "system"}, ClaimStatus.INFORMATION_REQUIRED: {"system"}},
    ClaimStatus.SUBMISSION_FAILED: {ClaimStatus.AWAITING_CUSTOMER_APPROVAL: {"customer", "agent"}, ClaimStatus.READY_FOR_REVIEW: {"system"}, ClaimStatus.SUBMITTED: {"system"}},
    ClaimStatus.SUBMITTED: {ClaimStatus.ACKNOWLEDGED: {"insurer", "adapter"}, ClaimStatus.UNDER_REVIEW: {"insurer"}, ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED: {"insurer"}},
    ClaimStatus.ACKNOWLEDGED: {ClaimStatus.UNDER_REVIEW: {"insurer"}, ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED: {"insurer"}},
    ClaimStatus.UNDER_REVIEW: {ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED: {"insurer"}, ClaimStatus.APPROVED: {"insurer"}, ClaimStatus.PARTIALLY_APPROVED: {"insurer"}, ClaimStatus.DECLINED: {"insurer"}},
    ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED: {ClaimStatus.UNDER_REVIEW: {"customer", "insurer", "system"}, ClaimStatus.APPROVED: {"insurer"}, ClaimStatus.PARTIALLY_APPROVED: {"insurer"}, ClaimStatus.DECLINED: {"insurer"}},
    ClaimStatus.APPROVED: {ClaimStatus.SETTLED: {"insurer"}, ClaimStatus.REOPENED: {"insurer"}},
    ClaimStatus.PARTIALLY_APPROVED: {ClaimStatus.SETTLED: {"insurer"}, ClaimStatus.REOPENED: {"insurer"}},
    ClaimStatus.DECLINED: {ClaimStatus.REOPENED: {"insurer"}, ClaimStatus.CLOSED: {"insurer"}},
    ClaimStatus.SETTLED: {ClaimStatus.CLOSED: {"insurer"}, ClaimStatus.REOPENED: {"insurer"}},
    ClaimStatus.CLOSED: {ClaimStatus.REOPENED: {"insurer"}},
    ClaimStatus.REOPENED: {ClaimStatus.UNDER_REVIEW: {"insurer"}, ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED: {"insurer"}},
}

STATUS_LABELS = {
    "draft": {"en": "Draft", "zh": "草稿"}, "information_required": {"en": "Information required", "zh": "需补充信息"}, "ready_for_review": {"en": "Ready for your review", "zh": "待你确认"},
    "awaiting_customer_approval": {"en": "Awaiting your approval", "zh": "等待你的批准"}, "submitted": {"en": "Submitted", "zh": "已提交"}, "acknowledged": {"en": "Acknowledged by insurer", "zh": "保险公司已确认收到"},
    "under_review": {"en": "Under review", "zh": "审核中"}, "additional_information_requested": {"en": "Insurer requested more information", "zh": "保险公司要求补充材料"}, "decision_issued": {"en": "Decision issued", "zh": "已出具结论"},
    "approved": {"en": "Approved", "zh": "已批准"}, "partially_approved": {"en": "Partially approved", "zh": "部分批准"}, "declined": {"en": "Declined", "zh": "拒赔"}, "settled": {"en": "Settled", "zh": "已结案赔付"},
    "closed": {"en": "Closed", "zh": "已关闭"}, "reopened": {"en": "Reopened", "zh": "重新打开"}, "submission_failed": {"en": "Submission failed", "zh": "提交失败"},
}


def can_transition(from_status: str, to_status: str, actor_type: str) -> bool:
    return actor_type in TRANSITIONS.get(from_status, {}).get(to_status, set())


def transition(db: Session, claim: Claim, to_status: str, *, actor_type: str, actor_id: str | None, summary: str, data: dict | None = None, source: str | None = None, visible: bool = True) -> None:
    if claim.status == to_status:
        return
    if not can_transition(claim.status, to_status, actor_type):
        raise TransitionError(f"{actor_type} may not move a claim from {claim.status} to {to_status}")
    old = claim.status
    claim.status = to_status
    claim.status_updated_at = utcnow()
    claim.status_source = source or {"insurer": "insurer", "adapter": "adapter"}.get(actor_type, "platform")
    db.add(ClaimEvent(claim_id=claim.id, actor_type=actor_type, actor_id=actor_id, event_type="status_changed", summary=summary, data={"from": old, "to": to_status, **(data or {})}, visible_to_customer=visible))


def add_event(db: Session, claim: Claim, *, actor_type: str, actor_id: str | None, event_type: str, summary: str, data: dict | None = None, visible: bool = True) -> ClaimEvent:
    ev = ClaimEvent(claim_id=claim.id, actor_type=actor_type, actor_id=actor_id, event_type=event_type, summary=summary, data=data or {}, visible_to_customer=visible)
    db.add(ev)
    return ev


def new_reference(db: Session) -> str:
    year = date.today().year
    count = db.execute(select(func.count(Claim.id))).scalar_one()
    for _ in range(20):
        ref = f"CLM-{year}-{100000 + count + 1 + secrets.randbelow(900)}"
        if not db.execute(select(Claim.id).where(Claim.reference == ref)).first():
            return ref
    return f"CLM-{year}-{secrets.token_hex(3).upper()}"


# ----------------------------------------------------------------- queries ---


def policy_with_clauses(db: Session, policy_id: str | None) -> tuple[Policy | None, list[PolicyClause]]:
    if not policy_id:
        return None, []
    policy = db.get(Policy, policy_id)
    if not policy:
        return None, []
    clauses = db.execute(select(PolicyClause).options(selectinload(PolicyClause.policy)).where(PolicyClause.policy_id == policy.id).order_by(PolicyClause.position)).scalars().all()
    return policy, list(clauses)


def claim_documents(db: Session, claim: Claim) -> list[Document]:
    return list(db.execute(select(Document).where(Document.claim_id == claim.id).order_by(Document.created_at)).scalars().all())


def refresh_claim_analysis(db: Session, claim: Claim) -> Claim:
    db.flush()
    policy, clauses = policy_with_clauses(db, claim.policy_id)
    docs = claim_documents(db, claim)
    claim.checklist = checklist_svc.build_checklist(claim, clauses, docs)
    if policy:
        claim.coverage_view = coverage_svc.coverage_view(policy, clauses, incident_type=claim.incident_type, description=claim.description, estimated_loss=claim.estimated_loss)
    else:
        claim.coverage_view = {"verdict": "no_policy", "summary": {"en": "No policy is linked to this claim yet.", "zh": "该理赔尚未关联保单。"}}
    claim.completeness = completeness_svc.assess(claim, policy, claim.checklist, docs)
    if claim.status in {ClaimStatus.DRAFT, ClaimStatus.INFORMATION_REQUIRED, ClaimStatus.READY_FOR_REVIEW}:
        target = ClaimStatus.READY_FOR_REVIEW if claim.completeness["ready"] else ClaimStatus.INFORMATION_REQUIRED
        if target != claim.status:
            transition(db, claim, target, actor_type="system", actor_id=None, summary={"ready_for_review": "Checklist complete: ready for your review", "information_required": "Checklist has outstanding required items"}[target], data={"progress": claim.completeness["progress"]})
    db.flush()
    return claim


# ------------------------------------------------------------------ drafts ---

UPDATABLE_FIELDS = {"policy_id", "incident_type", "incident_date", "incident_time", "location", "description", "items_involved", "estimated_loss", "loss_unknown", "currency", "third_party_involved", "authorities_involved", "authority_reference", "actions_taken", "safety_concern", "language"}


def create_draft(db: Session, user: User, data: dict, *, actor_type: str = "customer", run_id: str | None = None) -> Claim:
    policy = db.get(Policy, data.get("policy_id")) if data.get("policy_id") else None
    if policy and policy.user_id != user.id:
        raise PermissionError("policy does not belong to this customer")
    insurance_type = data.get("insurance_type") or (policy.insurance_type if policy else "home")
    claim = Claim(
        reference=new_reference(db),
        user_id=user.id,
        policy_id=policy.id if policy else None,
        insurer_org_id=policy.insurer_org_id if policy else None,
        insurance_type=insurance_type,
        incident_type=data.get("incident_type") or "other",
        incident_date=_as_date(data.get("incident_date")),
        incident_time=data.get("incident_time"),
        location=data.get("location"),
        description=data.get("description") or "",
        items_involved=data.get("items_involved") or [],
        estimated_loss=_as_float(data.get("estimated_loss")),
        loss_unknown=bool(data.get("loss_unknown")) or data.get("estimated_loss") in (None, "", "unknown"),
        currency=data.get("currency") or (policy.currency if policy else "GBP"),
        third_party_involved=data.get("third_party_involved"),
        authorities_involved=data.get("authorities_involved"),
        authority_reference=data.get("authority_reference"),
        actions_taken=data.get("actions_taken"),
        safety_concern=bool(data.get("safety_concern")),
        language=data.get("language") or user.locale or "en",
        status=ClaimStatus.DRAFT,
    )
    db.add(claim)
    db.flush()
    add_event(db, claim, actor_type=actor_type, actor_id=user.id if actor_type == "customer" else None, event_type="created", summary="Claim draft created" if claim.language != "zh" else "创建理赔草稿", data={"run_id": run_id} if run_id else {})
    refresh_claim_analysis(db, claim)
    audit.record(db, actor_type=actor_type, actor_id=user.id, action="claim.create_draft", target_type="claim", target_id=claim.id, details={"reference": claim.reference, "incident_type": claim.incident_type}, commit=False)
    return claim


def update_draft(db: Session, claim: Claim, data: dict, *, actor_type: str = "customer", actor_id: str | None = None) -> Claim:
    if claim.status not in ClaimStatus.PRE_SUBMISSION:
        raise TransitionError("claim can no longer be edited after submission; respond to the insurer instead")
    changed = []
    for key, value in data.items():
        if key not in UPDATABLE_FIELDS:
            continue
        if key == "policy_id":
            policy = db.get(Policy, value) if value else None
            if policy and policy.user_id != claim.user_id:
                raise PermissionError("policy does not belong to this customer")
            claim.policy_id = policy.id if policy else None
            claim.insurer_org_id = policy.insurer_org_id if policy else None
            if policy:
                claim.insurance_type = policy.insurance_type
                claim.currency = policy.currency
            changed.append(key)
            continue
        if key == "incident_date":
            value = _as_date(value)
        if key == "estimated_loss":
            value = _as_float(value)
            if value is None:
                claim.loss_unknown = True
            else:
                claim.loss_unknown = False
        setattr(claim, key, value)
        changed.append(key)
    if changed:
        add_event(db, claim, actor_type=actor_type, actor_id=actor_id, event_type="updated", summary=("Claim details updated: " if claim.language != "zh" else "更新理赔信息：") + ", ".join(changed), data={"fields": changed})
        if claim.status == ClaimStatus.AWAITING_CUSTOMER_APPROVAL:
            transition(db, claim, ClaimStatus.READY_FOR_REVIEW, actor_type="system", actor_id=None, summary="Details changed after approval was prepared; please review again")
    refresh_claim_analysis(db, claim)
    return claim


def attach_document(db: Session, claim: Claim, document: Document, item_key: str | None, *, actor_type: str, actor_id: str | None) -> Claim:
    document.claim_id = claim.id
    if item_key:
        document.checklist_item_key = item_key
    db.flush()
    add_event(db, claim, actor_type=actor_type, actor_id=actor_id, event_type="document_uploaded", summary=f"Uploaded {document.filename}" + (f" for {item_key}" if item_key else ""), data={"document_id": document.id, "doc_type": document.doc_type, "injection_flags": len(document.injection_flags or [])})
    if claim.status == ClaimStatus.AWAITING_CUSTOMER_APPROVAL:
        transition(db, claim, ClaimStatus.READY_FOR_REVIEW, actor_type="system", actor_id=None, summary="A document was added after approval was prepared; please review again")
    refresh_claim_analysis(db, claim)
    return claim


# --------------------------------------------------------------- approvals ---


@dataclass
class ApprovalCreated:
    approval: ApprovalRequest
    token: str


def submission_payload(db: Session, claim: Claim) -> dict:
    docs = claim_documents(db, claim)
    org = db.get(Organization, claim.insurer_org_id) if claim.insurer_org_id else None
    letter = latest_cover_letter(db, claim)
    return {
        "claim_id": claim.id,
        "claim_reference": claim.reference,
        "policy_id": claim.policy_id,
        "recipient": {"organization": org.name if org else None, "channel": "mock_insurer_api" if org else "assisted", "email": org.claims_email if org else None},
        "incident": {"type": claim.incident_type, "date": claim.incident_date.isoformat() if claim.incident_date else None, "time": claim.incident_time, "location": claim.location, "description": claim.description, "items_involved": claim.items_involved, "estimated_loss": claim.estimated_loss, "loss_unknown": claim.loss_unknown, "currency": claim.currency, "third_party_involved": claim.third_party_involved, "authorities_involved": claim.authorities_involved, "authority_reference": claim.authority_reference, "actions_taken": claim.actions_taken},
        "documents": [{"id": d.id, "filename": d.filename, "sha256": d.sha256} for d in docs],
        "cover_letter_id": letter.id if letter else None,
        "cover_letter_hash": payload_hash({"s": letter.subject, "b": letter.body}) if letter else None,
    }


def prepare_submission_approval(db: Session, user: User, claim: Claim, *, run_id: str | None = None, actor_type: str = "customer") -> ApprovalCreated:
    if claim.status not in {ClaimStatus.READY_FOR_REVIEW, ClaimStatus.SUBMISSION_FAILED, ClaimStatus.AWAITING_CUSTOMER_APPROVAL, ClaimStatus.INFORMATION_REQUIRED}:
        raise TransitionError(f"claim in status {claim.status} cannot be submitted")
    if not claim.policy_id:
        raise TransitionError("link a policy before submitting")
    if not claim.insurer_org_id:
        raise TransitionError("this policy is not connected to an insurer on the platform, so there is no channel to submit through; use assisted submission: download the claim pack, send it through the insurer's own channel, then mark the claim as sent")
    if not claim.completeness.get("ready"):
        raise TransitionError("the claim pack still has required items missing: " + ", ".join(claim.completeness.get("missing_fields", []) + claim.completeness.get("missing_required_documents", []) + claim.completeness.get("outstanding_information", []) + claim.completeness.get("outstanding_actions", [])))
    # expire any earlier pending approval for the same claim
    for prior in db.execute(select(ApprovalRequest).where(ApprovalRequest.target_id == claim.id, ApprovalRequest.action_type == "submit_claim", ApprovalRequest.status == "pending")).scalars():
        prior.status = "expired"
    payload = submission_payload(db, claim)
    raw, token_hash = new_approval_token()
    org = db.get(Organization, claim.insurer_org_id) if claim.insurer_org_id else None
    lang = claim.language or "en"
    summary = (
        f"Submit claim {claim.reference} to {org.name if org else 'the insurer'} with {len(payload['documents'])} document(s)."
        if lang != "zh" else f"将理赔 {claim.reference} 及 {len(payload['documents'])} 份文件提交给{org.name if org else '保险公司'}。"
    )
    consequences = [
        {"en": "The incident details, item list and the documents listed will be shared with the insurer.", "zh": "事件信息、物品清单和所列文件将发送给保险公司。"},
        {"en": "After submission the claim can no longer be edited here; further information goes through the insurer's requests.", "zh": "提交后将无法在此编辑理赔，后续补充通过保险公司的要求进行。"},
        {"en": "The insurer, not this assistant, decides whether the claim is covered.", "zh": "是否赔付由保险公司决定，而非本助手。"},
        {"en": "This demonstration environment sends the pack to a mock insurer endpoint.", "zh": "本演示环境会将材料包发送到模拟的保险公司接口。"},
    ]
    approval = ApprovalRequest(
        user_id=user.id, run_id=run_id, action_type="submit_claim", target_type="claim", target_id=claim.id, payload=payload, payload_hash=payload_hash(payload),
        summary=summary, consequences=consequences, status="pending", token_hash=token_hash, expires_at=utcnow() + timedelta(minutes=settings.approval_ttl_minutes),
    )
    db.add(approval)
    db.flush()
    if claim.status != ClaimStatus.AWAITING_CUSTOMER_APPROVAL:
        transition(db, claim, ClaimStatus.AWAITING_CUSTOMER_APPROVAL, actor_type=actor_type if actor_type in {"customer", "agent"} else "customer", actor_id=user.id, summary="Submission prepared; awaiting customer approval", data={"approval_id": approval.id})
    add_event(db, claim, actor_type=actor_type, actor_id=user.id if actor_type == "customer" else None, event_type="approval_requested", summary="Approval requested to submit the claim pack", data={"approval_id": approval.id, "documents": len(payload["documents"])})
    audit.record(db, actor_type=actor_type, actor_id=user.id, action="approval.create", target_type="approval", target_id=approval.id, details={"action_type": "submit_claim", "claim": claim.reference}, commit=False)
    return ApprovalCreated(approval=approval, token=raw)


def execute_submission(db: Session, claim: Claim, approval: ApprovalRequest, user: User) -> Submission:
    """Called only by the approvals service after the customer approved."""
    current = submission_payload(db, claim)
    if payload_hash(current) != approval.payload_hash:
        raise TransitionError("the claim changed after this approval was prepared; please review and approve again")
    policy, clauses = policy_with_clauses(db, claim.policy_id)
    docs = claim_documents(db, claim)
    letter = latest_cover_letter(db, claim)
    pack = pack_svc.build_pack(claim, policy, user, docs, claim.checklist, claim.completeness, claim.coverage_view, letter)
    pdf_path = pack_svc.render_pack_pdf(pack, claim)
    pack_doc = Document(owner_user_id=user.id, uploaded_by_user_id=user.id, claim_id=claim.id, filename=pdf_path.name, mime_type="application/pdf", size_bytes=pdf_path.stat().st_size, sha256=_sha256(pdf_path), storage_path=str(pdf_path), doc_type="claim_pack", extraction_status="not_applicable", extraction_meta={"generated": True})
    db.add(pack_doc)
    org = db.get(Organization, claim.insurer_org_id) if claim.insurer_org_id else None
    adapter = get_adapter(org.code if org else None, org.integration_mode if org else "mock")
    idem = f"claim:{claim.id}:approval:{approval.id}"
    sub = Submission(claim_id=claim.id, idempotency_key=idem, adapter=adapter.name, mode="integrated", request_payload={k: v for k, v in pack.items() if k not in {"customer"}} | {"customer": {"customer_id": user.id}}, approval_id=approval.id, status="failed")
    db.add(sub)
    try:
        result = adapter.submit_claim(pack, idempotency_key=idem)
    except AdapterError as exc:
        sub.status = "failed"
        sub.error = str(exc)
        sub.response_payload = {"error": str(exc)}
        transition(db, claim, ClaimStatus.SUBMISSION_FAILED, actor_type="system", actor_id=None, summary=f"Submission failed: {exc}. Nothing was sent; you can retry.", data={"submission_id": sub.id})
        db.add(Notification(user_id=user.id, kind="submission_failed", title="Claim submission failed" if claim.language != "zh" else "理赔提交失败", body=f"{claim.reference}: {exc}", link=f"/claims/{claim.id}"))
        audit.record(db, actor_type="system", actor_id=None, action="claim.submit", target_type="claim", target_id=claim.id, outcome="failure", details={"error": str(exc), "adapter": adapter.name}, commit=False)
        return sub
    sub.response_payload = {"status": result.status, "insurer_reference": result.insurer_reference, "mock": result.is_mock, **result.raw}
    if not result.ok:
        sub.status = "failed"
        sub.error = result.error or result.status
        transition(db, claim, ClaimStatus.SUBMISSION_FAILED, actor_type="system", actor_id=None, summary=f"Insurer channel rejected the submission: {sub.error}", data={"submission_id": sub.id})
        return sub
    sub.status = "succeeded"
    claim.submitted_at = utcnow()
    claim.insurer_reference = result.insurer_reference
    for d in docs:
        d.submitted_at = d.submitted_at or utcnow()
    transition(db, claim, ClaimStatus.SUBMITTED, actor_type="system", actor_id=None, summary=(f"Submitted via {adapter.name}; {'mock ' if result.is_mock else ''}acknowledgement reference {result.insurer_reference}"), data={"submission_id": sub.id, "insurer_reference": result.insurer_reference, "mock": result.is_mock}, source="adapter")
    if letter and letter.status in {"draft", "approved"}:
        letter.status = "sent"
        letter.sent_at = utcnow()
        letter.sender_user_id = user.id
        add_event(db, claim, actor_type="customer", actor_id=user.id, event_type="message_sent", summary=f"Cover letter sent: {letter.subject}", data={"communication_id": letter.id})
    notify_org_staff(db, claim.insurer_org_id, kind="new_claim", title=f"New claim received: {claim.reference}", body=f"{claim.incident_type.replace('_', ' ')} claim on policy {policy.policy_number if policy else '?'}", link=f"/insurer/claims/{claim.id}")
    audit.record(db, actor_type="customer", actor_id=user.id, action="claim.submit", target_type="claim", target_id=claim.id, details={"adapter": adapter.name, "insurer_reference": result.insurer_reference, "mock": result.is_mock, "idempotency_key": idem}, commit=False)
    return sub


def mark_sent_assisted(db: Session, claim: Claim, user: User, channel: str, reference: str | None) -> None:
    """Assisted mode: the customer sent the pack through the insurer's own
    channel and records that fact. The platform records it as the customer's
    statement, not as a confirmed receipt."""
    if claim.status not in {ClaimStatus.READY_FOR_REVIEW, ClaimStatus.AWAITING_CUSTOMER_APPROVAL, ClaimStatus.SUBMISSION_FAILED}:
        raise TransitionError(f"claim in status {claim.status} cannot be marked as sent")
    if not claim.completeness.get("ready"):
        raise TransitionError("finish the required items before sending the pack")
    if claim.status == ClaimStatus.READY_FOR_REVIEW:
        transition(db, claim, ClaimStatus.AWAITING_CUSTOMER_APPROVAL, actor_type="customer", actor_id=user.id, summary="Assisted submission recorded by customer")
    sub = Submission(claim_id=claim.id, idempotency_key=f"assisted:{claim.id}:{utcnow().isoformat()}", adapter="assisted", mode="assisted", request_payload={"channel": channel, "reference": reference}, response_payload={"customer_declared": True}, status="succeeded")
    db.add(sub)
    claim.submitted_at = utcnow()
    claim.insurer_reference = reference or claim.insurer_reference
    transition(db, claim, ClaimStatus.SUBMITTED, actor_type="system", actor_id=None, summary=f"Customer recorded sending the claim pack via {channel}" + (f" (reference {reference})" if reference else "") + "; not yet confirmed by the insurer", data={"mode": "assisted"}, source="customer")
    audit.record(db, actor_type="customer", actor_id=user.id, action="claim.mark_sent_assisted", target_type="claim", target_id=claim.id, details={"channel": channel}, commit=False)


def latest_cover_letter(db: Session, claim: Claim) -> Communication | None:
    return db.execute(select(Communication).where(Communication.claim_id == claim.id, Communication.direction == "outbound", Communication.kind == "claim_notification").order_by(Communication.created_at.desc()).limit(1)).scalar_one_or_none()


# ----------------------------------------------------------- insurer side ---


def assign(db: Session, claim: Claim, staff: User, handler: User | None) -> None:
    claim.assigned_handler_id = handler.id if handler else None
    if claim.first_response_at is None:
        claim.first_response_at = utcnow()
    add_event(db, claim, actor_type="insurer", actor_id=staff.id, event_type="assigned", summary=f"Assigned to {handler.full_name}" if handler else "Unassigned", data={"handler_id": handler.id if handler else None})
    if claim.status == ClaimStatus.SUBMITTED:
        transition(db, claim, ClaimStatus.ACKNOWLEDGED, actor_type="insurer", actor_id=staff.id, summary="Acknowledged by insurer")
        claim.acknowledged_at = claim.acknowledged_at or utcnow()
    audit.record(db, actor_type="insurer", actor_id=staff.id, action="claim.assign", target_type="claim", target_id=claim.id, details={"handler": handler.id if handler else None}, commit=False)


def set_status(db: Session, claim: Claim, staff: User, to_status: str, note: str | None = None) -> None:
    allowed = {ClaimStatus.ACKNOWLEDGED, ClaimStatus.UNDER_REVIEW, ClaimStatus.SETTLED, ClaimStatus.CLOSED, ClaimStatus.REOPENED}
    if to_status not in allowed:
        raise TransitionError("decisions are recorded with record_decision; other statuses are set by the workflow")
    if to_status == ClaimStatus.ACKNOWLEDGED:
        claim.acknowledged_at = claim.acknowledged_at or utcnow()
    if claim.first_response_at is None:
        claim.first_response_at = utcnow()
    transition(db, claim, to_status, actor_type="insurer", actor_id=staff.id, summary=note or f"Status set to {to_status.replace('_', ' ')}")
    db.add(Notification(user_id=claim.user_id, kind="status_changed", title=f"Claim {claim.reference}: {STATUS_LABELS.get(to_status, {}).get(claim.language or 'en', to_status)}", body=note or "", link=f"/claims/{claim.id}"))
    audit.record(db, actor_type="insurer", actor_id=staff.id, action="claim.set_status", target_type="claim", target_id=claim.id, details={"to": to_status}, commit=False)


def request_information(db: Session, claim: Claim, staff: User, message: str, items: list[str], due_date: date | None) -> InformationRequest:
    req = InformationRequest(claim_id=claim.id, requested_by_user_id=staff.id, message=message, requested_items=items, due_date=due_date, status="open")
    db.add(req)
    if claim.first_response_at is None:
        claim.first_response_at = utcnow()
    transition(db, claim, ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED, actor_type="insurer", actor_id=staff.id, summary="Insurer requested additional information", data={"items": items})
    db.add(Communication(claim_id=claim.id, policy_id=claim.policy_id, user_id=claim.user_id, insurer_org_id=claim.insurer_org_id, direction="inbound", channel="in_app", kind="information_request", subject=f"Further information needed — claim {claim.reference}", body=message, status="received", generated_by="insurer_staff", sent_at=utcnow(), sender_user_id=staff.id))
    db.add(Notification(user_id=claim.user_id, kind="information_requested", title=("Your insurer needs more information" if claim.language != "zh" else "保险公司要求补充材料"), body=message[:200], link=f"/claims/{claim.id}"))
    audit.record(db, actor_type="insurer", actor_id=staff.id, action="claim.request_information", target_type="claim", target_id=claim.id, details={"items": items}, commit=False)
    return req


def respond_information_request(db: Session, claim: Claim, user: User, req: InformationRequest, response_text: str, document_ids: list[str]) -> None:
    req.status = "answered"
    req.response_text = response_text
    req.responded_at = utcnow()
    now = utcnow()
    for d in db.execute(select(Document).where(Document.id.in_(document_ids), Document.claim_id == claim.id)).scalars():
        d.submitted_at = d.submitted_at or now
    db.add(Communication(claim_id=claim.id, policy_id=claim.policy_id, user_id=user.id, insurer_org_id=claim.insurer_org_id, direction="outbound", channel="in_app", kind="information_response", subject=f"Response to your request — claim {claim.reference}", body=response_text, status="sent", generated_by="user", sent_at=now, sender_user_id=user.id, referenced_document_ids=document_ids))
    add_event(db, claim, actor_type="customer", actor_id=user.id, event_type="information_provided", summary="Customer responded to the information request", data={"request_id": req.id, "documents": document_ids})
    if claim.status == ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED:
        transition(db, claim, ClaimStatus.UNDER_REVIEW, actor_type="customer", actor_id=user.id, summary="Information provided; back under insurer review")
    notify_org_staff(db, claim.insurer_org_id, kind="information_provided", title=f"Customer responded on {claim.reference}", body=response_text[:200], link=f"/insurer/claims/{claim.id}", only_user_id=claim.assigned_handler_id)
    audit.record(db, actor_type="customer", actor_id=user.id, action="claim.respond_information_request", target_type="claim", target_id=claim.id, details={"request_id": req.id, "documents": len(document_ids)}, commit=False)


def record_decision(db: Session, claim: Claim, staff: User, outcome: str, reason: str, clause_refs: list[str], approved_amount: float | None) -> ClaimDecision:
    if outcome not in {"approved", "partially_approved", "declined"}:
        raise TransitionError("outcome must be approved, partially_approved or declined")
    if not reason or len(reason.strip()) < 20:
        raise TransitionError("a decision needs a written reason of at least 20 characters")
    decision = ClaimDecision(claim_id=claim.id, decided_by_user_id=staff.id, outcome=outcome, reason=reason, clause_refs=clause_refs, approved_amount=approved_amount, currency=claim.currency)
    db.add(decision)
    if claim.first_response_at is None:
        claim.first_response_at = utcnow()
    transition(db, claim, outcome, actor_type="insurer", actor_id=staff.id, summary=f"Decision recorded by {staff.full_name}: {outcome.replace('_', ' ')}", data={"clause_refs": clause_refs, "approved_amount": approved_amount})
    add_event(db, claim, actor_type="insurer", actor_id=staff.id, event_type="decision_recorded", summary=f"{outcome.replace('_', ' ').capitalize()}: {reason[:160]}", data={"decision_id": decision.id})
    db.add(Communication(claim_id=claim.id, policy_id=claim.policy_id, user_id=claim.user_id, insurer_org_id=claim.insurer_org_id, direction="inbound", channel="in_app", kind="decision", subject=f"Decision on claim {claim.reference}", body=reason + (f"\n\nClauses relied on: {', '.join(clause_refs)}" if clause_refs else "") + (f"\nApproved amount: {approved_amount:,.2f} {claim.currency}" if approved_amount is not None else ""), status="received", generated_by="insurer_staff", sent_at=utcnow(), sender_user_id=staff.id))
    db.add(Notification(user_id=claim.user_id, kind="decision", title=("Decision issued on your claim" if claim.language != "zh" else "你的理赔已有结论"), body=f"{claim.reference}: {outcome.replace('_', ' ')}", link=f"/claims/{claim.id}"))
    audit.record(db, actor_type="insurer", actor_id=staff.id, action="claim.record_decision", target_type="claim", target_id=claim.id, details={"outcome": outcome, "approved_amount": approved_amount, "clause_refs": clause_refs}, commit=False)
    return decision


def notify_org_staff(db: Session, org_id: str | None, *, kind: str, title: str, body: str, link: str, only_user_id: str | None = None) -> None:
    if not org_id:
        return
    q = select(User).where(User.org_id == org_id, User.role.in_(list(Roles.INSURER_ROLES)), User.is_active.is_(True))
    for staff in db.execute(q).scalars():
        if only_user_id and staff.id != only_user_id and staff.role != Roles.INSURER_ADMIN:
            continue
        db.add(Notification(user_id=staff.id, kind=kind, title=title, body=body, link=link))


# ---------------------------------------------------------------- timeline ---


def timeline(db: Session, claim: Claim, *, for_customer: bool) -> list[dict]:
    q = select(ClaimEvent).where(ClaimEvent.claim_id == claim.id)
    if for_customer:
        q = q.where(ClaimEvent.visible_to_customer.is_(True))
    events = db.execute(q.order_by(ClaimEvent.created_at.asc(), ClaimEvent.id)).scalars().all()
    return [{"id": e.id, "at": e.created_at.isoformat(), "actor_type": e.actor_type, "event_type": e.event_type, "summary": e.summary, "data": e.data} for e in events]


# ----------------------------------------------------------------- helpers ---


def _as_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_float(value) -> float | None:
    if value in (None, "", "unknown"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sha256(path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()
