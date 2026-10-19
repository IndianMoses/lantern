from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import Principal, insurer_claim_or_404, require_insurer, require_insurer_admin
from ..models import AuditEvent, Claim, ClaimDecision, ClaimEvent, ClaimStatus, Communication, CustomerProfile, Document, InformationRequest, Organization, Policy, PolicyClause, Roles, Submission, User, utcnow
from ..services import claims as claim_svc
from ..services import integrity
from ..services import policies as pol_svc
from ..services.taxonomy import incident_label, insurance_label
from .documents import doc_out

router = APIRouter(prefix="/api/insurer", tags=["insurer"])

SLA_FIRST_RESPONSE_HOURS = 48
SLA_DECISION_DAYS = 10


def _org_claims_query(p: Principal):
    return select(Claim).where(Claim.insurer_org_id == p.org_id, Claim.status.notin_(list(ClaimStatus.PRE_SUBMISSION)))


def _customer_display(db: Session, user_id: str) -> dict:
    u = db.get(User, user_id)
    return {"id": user_id, "name": u.full_name if u else "Customer"}


def queue_row(db: Session, c: Claim, lang: str, names: dict[str, str]) -> dict:
    comp = c.completeness or {}
    outstanding = None
    if c.status == ClaimStatus.SUBMITTED:
        outstanding = "acknowledge_and_assign"
    elif c.status == ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED:
        outstanding = "waiting_for_customer"
    elif c.status in {ClaimStatus.ACKNOWLEDGED, ClaimStatus.UNDER_REVIEW, ClaimStatus.REOPENED}:
        outstanding = "assess"
    elif c.status in {ClaimStatus.APPROVED, ClaimStatus.PARTIALLY_APPROVED}:
        outstanding = "settle"
    age_days = (utcnow() - c.submitted_at).days if c.submitted_at else None
    sla_breach = bool(c.submitted_at and c.status in ClaimStatus.OPEN_WITH_INSURER and age_days is not None and age_days > SLA_DECISION_DAYS)
    return {
        "id": c.id, "reference": c.reference, "customer": {"id": c.user_id, "name": names.get(c.user_id, "Customer")}, "insurance_type": c.insurance_type, "insurance_type_label": insurance_label(c.insurance_type, lang),
        "incident_type": c.incident_type, "incident_type_label": incident_label(c.incident_type, lang), "incident_date": c.incident_date.isoformat() if c.incident_date else None,
        "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None, "status": c.status, "status_label": claim_svc.STATUS_LABELS.get(c.status, {}).get(lang, c.status), "completeness": comp.get("progress"), "completeness_ready": comp.get("ready"),
        "assigned_handler_id": c.assigned_handler_id, "assigned_handler": names.get(c.assigned_handler_id) if c.assigned_handler_id else None, "priority": c.priority, "updated_at": c.updated_at.isoformat(), "outstanding_action": outstanding,
        "estimated_loss": c.estimated_loss, "currency": c.currency, "age_days": age_days, "sla_breach": sla_breach, "insurer_reference": c.insurer_reference,
        "checks": _check_summary(db, c),
    }


def _check_summary(db: Session, c: Claim) -> dict | None:
    row = integrity.latest(db, c)
    if not row:
        return None
    return {"overall_status": row.overall_status, "escalations": len(row.escalations)}


def _names(db: Session, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    return {u.id: u.full_name for u in db.execute(select(User).where(User.id.in_(ids))).scalars()}


@router.get("/dashboard")
def dashboard(date_from: date | None = None, date_to: date | None = None, insurance_type: str | None = None, handler_id: str | None = None, p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    claims = list(db.execute(_org_claims_query(p)).scalars())
    if insurance_type:
        claims = [c for c in claims if c.insurance_type == insurance_type]
    if handler_id:
        claims = [c for c in claims if c.assigned_handler_id == handler_id]
    if date_from:
        claims = [c for c in claims if c.submitted_at and c.submitted_at.date() >= date_from]
    if date_to:
        claims = [c for c in claims if c.submitted_at and c.submitted_at.date() <= date_to]
    ids = [c.id for c in claims]
    decisions = list(db.execute(select(ClaimDecision).where(ClaimDecision.claim_id.in_(ids))).scalars()) if ids else []
    dec_by_claim = {d.claim_id: d for d in decisions}
    policies = list(db.execute(select(Policy).where(Policy.insurer_org_id == p.org_id)).scalars())
    now = utcnow()
    week_ago = now - timedelta(days=7)
    open_claims = [c for c in claims if c.status in ClaimStatus.OPEN_WITH_INSURER]
    processing_days = [((dec_by_claim[c.id].created_at - c.submitted_at).total_seconds() / 86400) for c in claims if c.id in dec_by_claim and c.submitted_at]
    first_response_hours = [((c.first_response_at - c.submitted_at).total_seconds() / 3600) for c in claims if c.first_response_at and c.submitted_at]
    sla_first = sum(1 for c in claims if c.submitted_at and ((c.first_response_at is None and (now - c.submitted_at).total_seconds() > SLA_FIRST_RESPONSE_HOURS * 3600 and c.status in ClaimStatus.OPEN_WITH_INSURER) or (c.first_response_at and (c.first_response_at - c.submitted_at).total_seconds() > SLA_FIRST_RESPONSE_HOURS * 3600)))
    sla_decision = sum(1 for c in open_claims if c.submitted_at and (now - c.submitted_at).days > SLA_DECISION_DAYS)
    comms = db.execute(select(func.count(Communication.id)).where(Communication.insurer_org_id == p.org_id, Communication.created_at >= now - timedelta(days=30))).scalar_one()
    renewals = sum(1 for x in policies if x.status == "active" and 0 <= (x.end_date - date.today()).days <= 60)
    submitted_amount = sum((c.estimated_loss or 0) for c in claims if c.submitted_at)
    approved_amount = sum((d.approved_amount or 0) for d in decisions if d.outcome in {"approved", "partially_approved"})
    settled_amount = sum((dec_by_claim[c.id].approved_amount or 0) for c in claims if c.status == ClaimStatus.SETTLED and c.id in dec_by_claim)
    outstanding_amount = sum((dec_by_claim[c.id].approved_amount or 0) for c in claims if c.status in {ClaimStatus.APPROVED, ClaimStatus.PARTIALLY_APPROVED} and c.id in dec_by_claim)
    currencies = Counter(c.currency for c in claims)
    main_currency = currencies.most_common(1)[0][0] if currencies else "GBP"

    # charts
    by_status = Counter(c.status for c in claims)
    by_type = Counter(c.insurance_type for c in claims)
    weeks: dict[str, int] = defaultdict(int)
    for c in claims:
        if c.submitted_at:
            monday = (c.submitted_at.date() - timedelta(days=c.submitted_at.weekday()))
            weeks[monday.isoformat()] += 1
    received_series = []
    start = (date.today() - timedelta(days=date.today().weekday())) - timedelta(weeks=11)
    for i in range(12):
        wk = (start + timedelta(weeks=i)).isoformat()
        received_series.append({"week": wk, "count": weeks.get(wk, 0)})
    proc_by_type: dict[str, list[float]] = defaultdict(list)
    for c in claims:
        if c.id in dec_by_claim and c.submitted_at:
            proc_by_type[c.insurance_type].append((dec_by_claim[c.id].created_at - c.submitted_at).total_seconds() / 86400)
    outcomes_by_month: dict[str, Counter] = defaultdict(Counter)
    for d in decisions:
        outcomes_by_month[d.created_at.strftime("%Y-%m")][d.outcome] += 1
    staff = list(db.execute(select(User).where(User.org_id == p.org_id, User.role.in_(list(Roles.INSURER_ROLES)))).scalars())
    workload = [{"handler_id": s.id, "handler": s.full_name, "open": sum(1 for c in open_claims if c.assigned_handler_id == s.id), "awaiting_customer": sum(1 for c in claims if c.assigned_handler_id == s.id and c.status == ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED)} for s in staff]
    workload.append({"handler_id": None, "handler": "Unassigned", "open": sum(1 for c in open_claims if not c.assigned_handler_id), "awaiting_customer": 0})
    docs = list(db.execute(select(Document).where(Document.claim_id.in_(ids), Document.submitted_at.isnot(None))).scalars()) if ids else []
    doc_outcomes = Counter(d.extraction_status for d in docs)
    flagged_docs = sum(1 for d in docs if d.injection_flags)
    org = db.get(Organization, p.org_id)
    return {
        "organization": {"id": org.id, "name": org.name, "name_zh": org.name_zh, "integration_mode": org.integration_mode},
        "filters": {"date_from": date_from.isoformat() if date_from else None, "date_to": date_to.isoformat() if date_to else None, "insurance_type": insurance_type, "handler_id": handler_id},
        "metrics": {
            "total_policies_managed": {"value": len(policies), "definition": "Policies administered by this organisation on the platform"},
            "active_policies": {"value": sum(1 for x in policies if x.status == "active"), "definition": "Policies with status active"},
            "new_claims_7d": {"value": sum(1 for c in claims if c.submitted_at and c.submitted_at >= week_ago), "definition": "Claims submitted in the last 7 days"},
            "open_claims": {"value": len(open_claims), "definition": "Submitted, acknowledged, under review, awaiting information or reopened"},
            "awaiting_customer_information": {"value": by_status.get(ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED, 0), "definition": "Claims where the insurer is waiting on the customer"},
            "awaiting_handler_action": {"value": sum(1 for c in claims if c.status in {ClaimStatus.SUBMITTED, ClaimStatus.ACKNOWLEDGED, ClaimStatus.UNDER_REVIEW, ClaimStatus.REOPENED}), "definition": "Claims where the next step is the handler's"},
            "decided": {"value": sum(1 for c in claims if c.id in dec_by_claim), "definition": "Claims with a recorded decision"},
            "settled": {"value": by_status.get(ClaimStatus.SETTLED, 0), "definition": "Claims marked settled"},
            "avg_processing_days": {"value": round(sum(processing_days) / len(processing_days), 1) if processing_days else None, "definition": "Mean days from submission to decision, decided claims only"},
            "avg_first_response_hours": {"value": round(sum(first_response_hours) / len(first_response_hours), 1) if first_response_hours else None, "definition": "Mean hours from submission to first handler action"},
            "sla_breaches": {"value": sla_first + sla_decision, "definition": f"First response over {SLA_FIRST_RESPONSE_HOURS}h or open longer than {SLA_DECISION_DAYS} days", "first_response": sla_first, "decision": sla_decision},
            "renewals_next_60_days": {"value": renewals, "definition": "Active policies ending within 60 days"},
            "communications_30d": {"value": comms, "definition": "Messages exchanged with customers in the last 30 days"},
            "amounts": {
                "currency": main_currency,
                "submitted": {"value": round(submitted_amount, 2), "definition": "Sum of customer estimated losses on submitted claims (not reserves)"},
                "approved": {"value": round(approved_amount, 2), "definition": "Sum of approved amounts in recorded decisions"},
                "settled": {"value": round(settled_amount, 2), "definition": "Approved amounts on claims marked settled"},
                "outstanding": {"value": round(outstanding_amount, 2), "definition": "Approved but not yet settled"},
                "average_settlement": {"value": round(settled_amount / max(1, by_status.get(ClaimStatus.SETTLED, 0)), 2) if by_status.get(ClaimStatus.SETTLED) else None, "definition": "Settled amount divided by settled claims"},
            },
            "claim_frequency": {"value": round(len([c for c in claims if c.submitted_at]) / max(1, len(policies)), 3), "definition": "Submitted claims per policy managed"},
        },
        "charts": {
            "claims_by_status": [{"status": s, "label": claim_svc.STATUS_LABELS.get(s, {}).get(p.lang, s), "count": n} for s, n in sorted(by_status.items(), key=lambda kv: -kv[1])],
            "claims_by_type": [{"type": t, "label": insurance_label(t, p.lang), "count": n} for t, n in by_type.items()],
            "claims_received_weekly": received_series,
            "avg_processing_days_by_type": [{"type": t, "label": insurance_label(t, p.lang), "days": round(sum(v) / len(v), 1), "n": len(v)} for t, v in proc_by_type.items()],
            "outcomes_by_month": [{"month": m, **dict(cnt)} for m, cnt in sorted(outcomes_by_month.items())],
            "workload_by_handler": workload,
            "document_processing": [{"status": s, "count": n} for s, n in doc_outcomes.items()] + [{"status": "instruction_flags", "count": flagged_docs}],
        },
        "data_note": "Demonstration data: all customers, policies and claims are fictional. Metrics computed live from the database.",
        "generated_at": now.isoformat(),
    }


@router.get("/claims")
def queue(status: str | None = None, insurance_type: str | None = None, handler: str | None = None, q: str | None = None, sort: str = "-updated_at", page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    query = _org_claims_query(p)
    if status:
        query = query.where(Claim.status.in_(status.split(",")))
    if insurance_type:
        query = query.where(Claim.insurance_type == insurance_type)
    if handler == "me":
        query = query.where(Claim.assigned_handler_id == p.id)
    elif handler == "unassigned":
        query = query.where(Claim.assigned_handler_id.is_(None))
    elif handler:
        query = query.where(Claim.assigned_handler_id == handler)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Claim.reference.ilike(like), Claim.description.ilike(like), Claim.insurer_reference.ilike(like), Claim.location.ilike(like)))
    sort_col = {"updated_at": Claim.updated_at, "submitted_at": Claim.submitted_at, "incident_date": Claim.incident_date, "status": Claim.status, "priority": Claim.priority, "reference": Claim.reference}.get(sort.lstrip("-"), Claim.updated_at)
    query = query.order_by(sort_col.desc() if sort.startswith("-") else sort_col.asc())
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    rows = db.execute(query.offset((page - 1) * page_size).limit(page_size)).scalars().all()
    names = _names(db, {c.user_id for c in rows} | {c.assigned_handler_id for c in rows if c.assigned_handler_id})
    return {"claims": [queue_row(db, c, p.lang, names) for c in rows], "total": total, "page": page, "page_size": page_size, "statuses": sorted(claim_svc.STATUS_LABELS.keys())}


@router.get("/claims/{claim_id}")
def workspace(claim_id: str, p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    c = insurer_claim_or_404(db, p, claim_id)
    lang = p.lang
    customer = db.get(User, c.user_id)
    policy = db.get(Policy, c.policy_id) if c.policy_id else None
    clauses = db.execute(select(PolicyClause).where(PolicyClause.policy_id == policy.id).order_by(PolicyClause.position)).scalars().all() if policy else []
    docs = [d for d in claim_svc.claim_documents(db, c) if d.submitted_at or d.doc_type == "claim_pack"]
    reqs = db.execute(select(InformationRequest).where(InformationRequest.claim_id == c.id).order_by(InformationRequest.created_at.desc())).scalars().all()
    comms = db.execute(select(Communication).where(Communication.claim_id == c.id).order_by(Communication.created_at.asc())).scalars().all()
    decisions = db.execute(select(ClaimDecision).where(ClaimDecision.claim_id == c.id).order_by(ClaimDecision.created_at.desc())).scalars().all()
    subs = db.execute(select(Submission).where(Submission.claim_id == c.id).order_by(Submission.created_at.desc())).scalars().all()
    notes = db.execute(select(ClaimEvent).where(ClaimEvent.claim_id == c.id, ClaimEvent.event_type == "internal_note").order_by(ClaimEvent.created_at.desc())).scalars().all()
    audits = db.execute(select(AuditEvent).where(AuditEvent.target_id == c.id).order_by(AuditEvent.seq.desc()).limit(50)).scalars().all()
    names = _names(db, {c.user_id} | {c.assigned_handler_id} - {None} | {n.actor_id for n in notes if n.actor_id} | {d.decided_by_user_id for d in decisions} | {r.requested_by_user_id for r in reqs})
    # mark customer messages read by insurer
    for m in comms:
        if m.direction == "outbound" and not m.read_at:
            m.read_at = utcnow()
    db.commit()
    return {
        "claim": queue_row(db, c, lang, names) | {"description": c.description, "incident_time": c.incident_time, "location": c.location, "items_involved": c.items_involved, "loss_unknown": c.loss_unknown, "third_party_involved": c.third_party_involved, "authorities_involved": c.authorities_involved, "authority_reference": c.authority_reference, "actions_taken": c.actions_taken, "safety_concern": c.safety_concern, "language": c.language, "acknowledged_at": c.acknowledged_at.isoformat() if c.acknowledged_at else None, "first_response_at": c.first_response_at.isoformat() if c.first_response_at else None, "status_source": c.status_source},
        "customer": {"id": customer.id, "name": customer.full_name, "email": customer.email, "locale": customer.locale},
        "policy": pol_svc.policy_card(policy, lang) if policy else None,
        "clauses": [pol_svc.clause_dict(cl, policy.policy_number) for cl in clauses],
        "documents": [doc_out(d) | {"extracted_excerpt": (d.extracted_text or "")[:600]} for d in docs],
        "checklist": c.checklist,
        "completeness": c.completeness,
        "coverage_reading": c.coverage_view | {"label": "Customer side reading of the wording, generated by the platform. Not an insurer determination."},
        "information_requests": [{"id": r.id, "message": r.message, "requested_items": r.requested_items, "due_date": r.due_date.isoformat() if r.due_date else None, "status": r.status, "response_text": r.response_text, "responded_at": r.responded_at.isoformat() if r.responded_at else None, "requested_by": names.get(r.requested_by_user_id), "created_at": r.created_at.isoformat()} for r in reqs],
        "communications": [{"id": m.id, "direction": m.direction, "kind": m.kind, "subject": m.subject, "body": m.body, "status": m.status, "generated_by": m.generated_by, "sent_at": m.sent_at.isoformat() if m.sent_at else None, "created_at": m.created_at.isoformat(), "referenced_document_ids": m.referenced_document_ids} for m in comms if m.status in {"sent", "received"}],
        "decisions": [{"id": d.id, "outcome": d.outcome, "reason": d.reason, "clause_refs": d.clause_refs, "approved_amount": d.approved_amount, "currency": d.currency, "decided_by": names.get(d.decided_by_user_id), "created_at": d.created_at.isoformat()} for d in decisions],
        "submissions": [{"id": s.id, "status": s.status, "adapter": s.adapter, "mode": s.mode, "insurer_reference": s.response_payload.get("insurer_reference"), "mock": bool(s.response_payload.get("mock")), "error": s.error, "created_at": s.created_at.isoformat()} for s in subs],
        "internal_notes": [{"id": n.id, "author": names.get(n.actor_id, "Staff"), "text": n.summary, "created_at": n.created_at.isoformat()} for n in notes],
        "timeline": claim_svc.timeline(db, c, for_customer=False),
        "checks": integrity.to_dict(integrity.latest(db, c), audience="insurer"),
        "audit": [{"seq": a.seq, "action": a.action, "actor_type": a.actor_type, "outcome": a.outcome, "details": a.details, "created_at": a.created_at.isoformat(), "hash": a.hash[:12]} for a in audits],
        "allowed_statuses": [s for s in (ClaimStatus.ACKNOWLEDGED, ClaimStatus.UNDER_REVIEW, ClaimStatus.SETTLED, ClaimStatus.CLOSED, ClaimStatus.REOPENED) if claim_svc.can_transition(c.status, s, "insurer")],
        "can_decide": claim_svc.can_transition(c.status, "approved", "insurer"),
    }


class AssignIn(BaseModel):
    handler_id: str | None = Field(None, description="Staff user id, 'me', or null to unassign")


@router.post("/claims/{claim_id}/assign")
def assign(claim_id: str, body: AssignIn, p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    c = insurer_claim_or_404(db, p, claim_id)
    handler = None
    if body.handler_id == "me":
        handler = p.user
    elif body.handler_id:
        handler = db.get(User, body.handler_id)
        if not handler or handler.org_id != p.org_id or handler.role not in Roles.INSURER_ROLES:
            raise HTTPException(status_code=400, detail="Handler must be staff of this organisation")
    claim_svc.assign(db, c, p.user, handler)
    db.commit()
    return {"ok": True, "status": c.status, "assigned_handler_id": c.assigned_handler_id}


class StatusIn(BaseModel):
    status: str
    note: str | None = Field(None, max_length=1000)


@router.post("/claims/{claim_id}/status")
def set_status(claim_id: str, body: StatusIn, p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    c = insurer_claim_or_404(db, p, claim_id)
    try:
        claim_svc.set_status(db, c, p.user, body.status, body.note)
    except claim_svc.TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"ok": True, "status": c.status}


class RequestInfoIn(BaseModel):
    message: str = Field(min_length=10, max_length=4000)
    requested_items: list[str] = Field(default_factory=list, max_length=20)
    due_date: date | None = None


@router.post("/claims/{claim_id}/request-information")
def request_information(claim_id: str, body: RequestInfoIn, p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    c = insurer_claim_or_404(db, p, claim_id)
    try:
        req = claim_svc.request_information(db, c, p.user, body.message, body.requested_items, body.due_date)
    except claim_svc.TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"ok": True, "request_id": req.id, "status": c.status}


class DecisionIn(BaseModel):
    outcome: str = Field(pattern="^(approved|partially_approved|declined)$")
    reason: str = Field(min_length=20, max_length=4000)
    clause_refs: list[str] = Field(default_factory=list, max_length=20)
    approved_amount: float | None = Field(None, ge=0)


@router.post("/claims/{claim_id}/decision")
def decision(claim_id: str, body: DecisionIn, p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    c = insurer_claim_or_404(db, p, claim_id)
    if body.outcome in {"approved", "partially_approved"} and body.approved_amount is None:
        raise HTTPException(status_code=400, detail="An approved amount is required for approvals")
    try:
        d = claim_svc.record_decision(db, c, p.user, body.outcome, body.reason, body.clause_refs, body.approved_amount)
    except claim_svc.TransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"ok": True, "decision_id": d.id, "status": c.status}


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/claims/{claim_id}/notes")
def add_note(claim_id: str, body: NoteIn, p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    c = insurer_claim_or_404(db, p, claim_id)
    ev = claim_svc.add_event(db, c, actor_type="insurer", actor_id=p.id, event_type="internal_note", summary=body.text[:500], data={"full_text": body.text}, visible=False)
    audit.record(db, actor_type="insurer", actor_id=p.id, action="claim.note", target_type="claim", target_id=c.id, commit=False)
    db.commit()
    return {"ok": True, "note_id": ev.id}


@router.get("/customers")
def customers(q: str | None = None, p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    pol_rows = db.execute(select(Policy).where(Policy.insurer_org_id == p.org_id)).scalars().all()
    claim_rows = list(db.execute(_org_claims_query(p)).scalars())
    by_user: dict[str, dict] = {}
    for x in pol_rows:
        d = by_user.setdefault(x.user_id, {"policies": 0, "active_policies": 0, "open_claims": 0, "claims": 0, "next_renewal": None})
        d["policies"] += 1
        d["active_policies"] += 1 if x.status == "active" else 0
        if x.status == "active" and (d["next_renewal"] is None or x.end_date.isoformat() < d["next_renewal"]):
            d["next_renewal"] = x.end_date.isoformat()
    for c in claim_rows:
        d = by_user.setdefault(c.user_id, {"policies": 0, "active_policies": 0, "open_claims": 0, "claims": 0, "next_renewal": None})
        d["claims"] += 1
        d["open_claims"] += 1 if c.status in ClaimStatus.OPEN_WITH_INSURER else 0
    users = {u.id: u for u in db.execute(select(User).where(User.id.in_(list(by_user)))).scalars()} if by_user else {}
    out = []
    for uid, d in by_user.items():
        u = users.get(uid)
        if not u:
            continue
        if q and q.lower() not in u.full_name.lower() and q.lower() not in u.email.lower():
            continue
        out.append({"id": uid, "name": u.full_name, "email": u.email, "locale": u.locale, **d})
    out.sort(key=lambda r: (-r["open_claims"], r["name"]))
    return {"customers": out}


@router.get("/customers/{customer_id}")
def customer_detail(customer_id: str, p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    u = db.get(User, customer_id)
    pols = db.execute(select(Policy).where(Policy.insurer_org_id == p.org_id, Policy.user_id == customer_id)).scalars().all()
    claims = list(db.execute(_org_claims_query(p).where(Claim.user_id == customer_id)).scalars())
    if not u or (not pols and not claims):
        raise HTTPException(status_code=404, detail="Customer not found")
    names = _names(db, {customer_id} | {c.assigned_handler_id for c in claims if c.assigned_handler_id})
    comms = db.execute(select(Communication).where(Communication.insurer_org_id == p.org_id, Communication.user_id == customer_id, Communication.status.in_(["sent", "received"])).order_by(Communication.created_at.desc()).limit(20)).scalars().all()
    return {
        "customer": {"id": u.id, "name": u.full_name, "email": u.email, "locale": u.locale},
        "policies": [pol_svc.policy_card(x, p.lang) for x in pols],
        "claims": [queue_row(db, c, p.lang, names) for c in claims],
        "communications": [{"id": m.id, "direction": m.direction, "subject": m.subject, "sent_at": m.sent_at.isoformat() if m.sent_at else None, "claim_id": m.claim_id} for m in comms],
    }


@router.get("/policies")
def org_policies(p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    pols = db.execute(select(Policy).where(Policy.insurer_org_id == p.org_id).order_by(Policy.end_date)).scalars().all()
    names = _names(db, {x.user_id for x in pols})
    return {"policies": [pol_svc.policy_card(x, p.lang) | {"customer": {"id": x.user_id, "name": names.get(x.user_id)}} for x in pols]}


@router.get("/team")
def team(p: Principal = Depends(require_insurer), db: Session = Depends(get_db)):
    staff = db.execute(select(User).where(User.org_id == p.org_id, User.role.in_(list(Roles.INSURER_ROLES)), User.is_active.is_(True))).scalars().all()
    return {"team": [{"id": s.id, "name": s.full_name, "role": s.role, "email": s.email} for s in staff]}


@router.get("/audit")
def audit_log(limit: int = Query(100, ge=1, le=500), p: Principal = Depends(require_insurer_admin), db: Session = Depends(get_db)):
    staff_ids = {s.id for s in db.execute(select(User).where(User.org_id == p.org_id)).scalars()}
    claim_ids = {c.id for c in db.execute(_org_claims_query(p)).scalars()}
    rows = db.execute(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(2000)).scalars().all()
    scoped = [a for a in rows if (a.actor_id in staff_ids) or (a.target_id in claim_ids)][:limit]
    return {"events": [{"seq": a.seq, "action": a.action, "actor_type": a.actor_type, "actor_id": a.actor_id, "target_type": a.target_type, "target_id": a.target_id, "outcome": a.outcome, "details": a.details, "created_at": a.created_at.isoformat(), "hash": a.hash, "prev_hash": a.prev_hash} for a in scoped], "chain": audit.verify_chain(db)}
