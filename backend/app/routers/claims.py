from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import Principal, customer_claim_or_404, require_customer
from ..models import ClaimDecision, Communication, Document, InformationRequest, Organization, Policy, Submission, User
from ..services import approvals as approval_svc
from ..services import claim_pack as pack_svc
from ..services import claims as claim_svc
from ..services import communications as comm_svc
from ..services import coverage as coverage_svc
from ..services import policies as pol_svc
from ..services.taxonomy import INCIDENT_TYPES, incident_types_for, questions_for
from .documents import doc_out

router = APIRouter(prefix="/api/claims", tags=["claims"])


class ItemIn(BaseModel):
    label: str = Field(max_length=200)
    value: float | None = Field(None, ge=0)


class ClaimIn(BaseModel):
    policy_id: str | None = None
    insurance_type: str | None = None
    incident_type: str
    incident_date: date | None = None
    incident_time: str | None = Field(None, max_length=10)
    location: str | None = Field(None, max_length=200)
    description: str = Field("", max_length=4000)
    items_involved: list[ItemIn] = Field(default_factory=list, max_length=40)
    estimated_loss: float | None = Field(None, ge=0)
    loss_unknown: bool = False
    third_party_involved: bool | None = None
    authorities_involved: bool | None = None
    authority_reference: str | None = Field(None, max_length=80)
    actions_taken: str | None = Field(None, max_length=2000)
    safety_concern: bool = False
    extra_answers: dict[str, Any] = Field(default_factory=dict)


class ClaimPatch(BaseModel):
    fields: dict[str, Any]


def claim_detail(db: Session, claim, user: User, lang: str | None = None) -> dict:
    lang = lang or user.locale or "en"
    policy = db.get(Policy, claim.policy_id) if claim.policy_id else None
    org = db.get(Organization, claim.insurer_org_id) if claim.insurer_org_id else None
    docs = claim_svc.claim_documents(db, claim)
    reqs = db.execute(select(InformationRequest).where(InformationRequest.claim_id == claim.id).order_by(InformationRequest.created_at.desc())).scalars().all()
    comms = db.execute(select(Communication).where(Communication.claim_id == claim.id).order_by(Communication.created_at.asc())).scalars().all()
    decision = db.execute(select(ClaimDecision).where(ClaimDecision.claim_id == claim.id).order_by(ClaimDecision.created_at.desc()).limit(1)).scalar_one_or_none()
    subs = db.execute(select(Submission).where(Submission.claim_id == claim.id).order_by(Submission.created_at.desc())).scalars().all()
    pending = [a for a in approval_svc.pending_for_user(db, user) if a.target_id == claim.id or (a.target_type == "communication" and any(c.id == a.target_id for c in comms)) or (a.target_type == "information_request" and any(r.id == a.target_id for r in reqs))]
    handler = db.get(User, claim.assigned_handler_id) if claim.assigned_handler_id else None
    return {
        "claim": pol_svc.claim_summary(claim, lang) | {
            "incident_time": claim.incident_time, "location": claim.location, "description": claim.description, "items_involved": claim.items_involved, "loss_unknown": claim.loss_unknown,
            "third_party_involved": claim.third_party_involved, "authorities_involved": claim.authorities_involved, "authority_reference": claim.authority_reference, "actions_taken": claim.actions_taken, "safety_concern": claim.safety_concern,
            "language": claim.language, "acknowledged_at": claim.acknowledged_at.isoformat() if claim.acknowledged_at else None, "editable": claim.status in {"draft", "information_required", "ready_for_review", "awaiting_customer_approval", "submission_failed"},
        },
        "policy": pol_svc.policy_card(policy, lang) if policy else None,
        "insurer": {"name": org.name, "name_zh": org.name_zh, "claims_email": org.claims_email, "integration_mode": org.integration_mode} if org else None,
        "handler": {"name": handler.full_name} if handler else None,
        "checklist": claim.checklist,
        "completeness": claim.completeness,
        "coverage_view": claim.coverage_view,
        "documents": [doc_out(d) for d in docs],
        "information_requests": [{"id": r.id, "message": r.message, "requested_items": r.requested_items, "due_date": r.due_date.isoformat() if r.due_date else None, "status": r.status, "response_text": r.response_text, "responded_at": r.responded_at.isoformat() if r.responded_at else None, "created_at": r.created_at.isoformat()} for r in reqs],
        "communications": [{"id": c.id, "direction": c.direction, "kind": c.kind, "subject": c.subject, "body": c.body, "recipient": c.recipient, "status": c.status, "generated_by": c.generated_by, "sent_at": c.sent_at.isoformat() if c.sent_at else None, "created_at": c.created_at.isoformat(), "read_at": c.read_at.isoformat() if c.read_at else None, "referenced_document_ids": c.referenced_document_ids} for c in comms],
        "decision": ({"outcome": decision.outcome, "reason": decision.reason, "clause_refs": decision.clause_refs, "approved_amount": decision.approved_amount, "currency": decision.currency, "decided_at": decision.created_at.isoformat()} if decision else None),
        "submissions": [{"id": s.id, "status": s.status, "mode": s.mode, "adapter": s.adapter, "error": s.error, "insurer_reference": s.response_payload.get("insurer_reference"), "mock": bool(s.response_payload.get("mock")), "created_at": s.created_at.isoformat()} for s in subs],
        "pending_approvals": [approval_svc.serialize(a) for a in pending],
        "timeline": claim_svc.timeline(db, claim, for_customer=True),
        "questions": questions_for(claim.insurance_type, claim.incident_type),
    }


@router.get("")
def list_claims(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    from ..models import Claim

    claims = db.execute(select(Claim).where(Claim.user_id == p.id).order_by(Claim.updated_at.desc())).scalars().all()
    return {"claims": [pol_svc.claim_summary(c, p.lang) | {"description": c.description[:200], "location": c.location} for c in claims]}


@router.get("/incident-types")
def incident_types(insurance_type: str = "home"):
    return {"insurance_type": insurance_type, "incident_types": incident_types_for(insurance_type)}


@router.get("/questions")
def questions(insurance_type: str = "home", incident_type: str = "other"):
    if incident_type not in INCIDENT_TYPES:
        raise HTTPException(status_code=400, detail="Unknown incident type")
    return {"questions": questions_for(insurance_type, incident_type)}


class RankIn(BaseModel):
    incident_type: str
    description: str = Field("", max_length=4000)


@router.post("/rank-policies")
def rank_policies(body: RankIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    if body.incident_type not in INCIDENT_TYPES:
        raise HTTPException(status_code=400, detail="Unknown incident type")
    ranked = coverage_svc.rank_policies_for_incident(pol_svc.policies_with_clauses(db, p.id, only_active=True), incident_type=body.incident_type, description=body.description)
    return {"ranked": ranked}


@router.post("")
def create_claim(body: ClaimIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    if body.incident_type not in INCIDENT_TYPES:
        raise HTTPException(status_code=400, detail="Unknown incident type")
    data = body.model_dump()
    data["items_involved"] = [i.model_dump() for i in body.items_involved]
    extra = data.pop("extra_answers", {})
    if extra:
        data["actions_taken"] = (data.get("actions_taken") or "") + ("\n" if data.get("actions_taken") else "") + "; ".join(f"{k}: {v}" for k, v in extra.items() if v not in (None, ""))
    try:
        claim = claim_svc.create_draft(db, p.user, data, actor_type="customer")
    except PermissionError:
        raise HTTPException(status_code=404, detail="Policy not found")
    db.commit()
    return claim_detail(db, claim, p.user, p.lang)


@router.get("/{claim_id}")
def get_claim(claim_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    claim = customer_claim_or_404(db, p, claim_id)
    # mark inbound messages read
    now_read = False
    for c in db.execute(select(Communication).where(Communication.claim_id == claim.id, Communication.direction == "inbound", Communication.read_at.is_(None))).scalars():
        from ..models import utcnow

        c.read_at = utcnow()
        now_read = True
    if now_read:
        db.commit()
    return claim_detail(db, claim, p.user, p.lang)


@router.patch("/{claim_id}")
def update_claim(claim_id: str, body: ClaimPatch, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    claim = customer_claim_or_404(db, p, claim_id)
    fields = dict(body.fields)
    if "items_involved" in fields and isinstance(fields["items_involved"], list):
        fields["items_involved"] = [{"label": str(i.get("label", ""))[:200], "value": i.get("value")} for i in fields["items_involved"] if isinstance(i, dict)]
    if "incident_type" in fields and fields["incident_type"] not in INCIDENT_TYPES:
        raise HTTPException(status_code=400, detail="Unknown incident type")
    try:
        claim_svc.update_draft(db, claim, fields, actor_type="customer", actor_id=p.id)
    except claim_svc.TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionError:
        raise HTTPException(status_code=404, detail="Policy not found")
    db.commit()
    return claim_detail(db, claim, p.user, p.lang)


@router.post("/{claim_id}/refresh")
def refresh(claim_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    claim = customer_claim_or_404(db, p, claim_id)
    claim_svc.refresh_claim_analysis(db, claim)
    db.commit()
    return claim_detail(db, claim, p.user, p.lang)


@router.post("/{claim_id}/submit")
def submit(claim_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    claim = customer_claim_or_404(db, p, claim_id)
    try:
        created = claim_svc.prepare_submission_approval(db, p.user, claim, actor_type="customer")
    except claim_svc.TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"approval": approval_svc.serialize(created.approval, created.token), "claim": claim_detail(db, claim, p.user, p.lang)}


@router.get("/{claim_id}/pack")
def pack_preview(claim_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    claim = customer_claim_or_404(db, p, claim_id)
    policy, _ = claim_svc.policy_with_clauses(db, claim.policy_id)
    docs = claim_svc.claim_documents(db, claim)
    pack = pack_svc.build_pack(claim, policy, p.user, docs, claim.checklist, claim.completeness, claim.coverage_view, claim_svc.latest_cover_letter(db, claim))
    return {"pack": pack}


@router.post("/{claim_id}/pack/pdf")
def pack_pdf(claim_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    """Assisted mode: generate the pack as a PDF the customer can send through any channel."""
    claim = customer_claim_or_404(db, p, claim_id)
    policy, _ = claim_svc.policy_with_clauses(db, claim.policy_id)
    docs = [d for d in claim_svc.claim_documents(db, claim) if d.doc_type != "claim_pack"]
    pack = pack_svc.build_pack(claim, policy, p.user, docs, claim.checklist, claim.completeness, claim.coverage_view, claim_svc.latest_cover_letter(db, claim))
    path = pack_svc.render_pack_pdf(pack, claim)
    claim_svc.add_event(db, claim, actor_type="customer", actor_id=p.id, event_type="pack_generated", summary="Claim pack PDF generated for assisted submission", data={"file": path.name})
    audit.record(db, actor_type="customer", actor_id=p.id, action="claim.pack_pdf", target_type="claim", target_id=claim.id, commit=False)
    db.commit()
    return FileResponse(path, media_type="application/pdf", filename=path.name, headers={"Cache-Control": "private, no-store"})


class MarkSentIn(BaseModel):
    channel: str = Field(min_length=2, max_length=60, description="How the pack was sent: email, portal, post, phone")
    reference: str | None = Field(None, max_length=60)


@router.post("/{claim_id}/mark-sent")
def mark_sent(claim_id: str, body: MarkSentIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    claim = customer_claim_or_404(db, p, claim_id)
    try:
        claim_svc.mark_sent_assisted(db, claim, p.user, body.channel, body.reference)
    except claim_svc.TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return claim_detail(db, claim, p.user, p.lang)


class CommIn(BaseModel):
    kind: str = Field("progress_update")
    subject: str | None = Field(None, max_length=200)
    body: str | None = Field(None, max_length=8000)
    points: list[str] = Field(default_factory=list, max_length=10)
    documents: list[str] = Field(default_factory=list, max_length=10)


@router.post("/{claim_id}/communications")
def create_communication(claim_id: str, body: CommIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    claim = customer_claim_or_404(db, p, claim_id)
    if body.kind not in comm_svc.KINDS:
        raise HTTPException(status_code=400, detail="Unknown kind")
    policy = db.get(Policy, claim.policy_id) if claim.policy_id else None
    org = db.get(Organization, claim.insurer_org_id) if claim.insurer_org_id else None
    decision = db.execute(select(ClaimDecision).where(ClaimDecision.claim_id == claim.id).order_by(ClaimDecision.created_at.desc()).limit(1)).scalar_one_or_none()
    d = comm_svc.draft(body.kind, user=p.user, claim=claim, policy=policy, org=org, decision=decision, points=body.points, documents=body.documents, lang=claim.language or p.lang)
    comm = Communication(claim_id=claim.id, policy_id=claim.policy_id, user_id=p.id, insurer_org_id=claim.insurer_org_id, direction="outbound", channel="in_app", kind=body.kind, subject=body.subject or d["subject"], body=body.body or d["body"], recipient=d["recipient"], status="draft", generated_by="user" if body.body else "agent")
    db.add(comm)
    db.flush()
    claim_svc.add_event(db, claim, actor_type="customer", actor_id=p.id, event_type="draft_created", summary=f"Draft prepared: {comm.subject}", data={"communication_id": comm.id})
    db.commit()
    return {"communication": {"id": comm.id, "kind": comm.kind, "subject": comm.subject, "body": comm.body, "recipient": comm.recipient, "status": comm.status}}


class RespondIn(BaseModel):
    response_text: str = Field(min_length=1, max_length=4000)
    document_ids: list[str] = Field(default_factory=list, max_length=20)


@router.post("/{claim_id}/information-requests/{request_id}/respond")
def respond_to_request(claim_id: str, request_id: str, body: RespondIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    claim = customer_claim_or_404(db, p, claim_id)
    req = db.get(InformationRequest, request_id)
    if not req or req.claim_id != claim.id:
        raise HTTPException(status_code=404, detail="Request not found")
    docs = db.execute(select(Document.id).where(Document.id.in_(body.document_ids), Document.claim_id == claim.id)).scalars().all() if body.document_ids else []
    try:
        created = approval_svc.create_information_response_approval(db, p.user, claim, req, body.response_text, list(docs), actor_type="customer")
    except (approval_svc.ApprovalError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"approval": approval_svc.serialize(created.approval, created.token)}
