from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..db import get_db
from ..deps import Principal, customer_policy_or_404, require_customer
from ..models import Claim, Organization, Policy, PolicyClause, PolicyStatus
from ..services import policies as pol_svc
from ..services.extraction import guess_metadata, segment_clauses
from ..services.retrieval import detect_language
from ..services.taxonomy import INSURANCE_TYPES
from .documents import store_upload

router = APIRouter(prefix="/api/policies", tags=["policies"])


@router.get("")
def list_policies(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    pols = pol_svc.user_policies(db, p.id)
    open_claims = db.execute(select(Claim.policy_id).where(Claim.user_id == p.id, Claim.status.notin_(["settled", "closed"]))).scalars().all()
    counts: dict[str, int] = {}
    for pid in open_claims:
        if pid:
            counts[pid] = counts.get(pid, 0) + 1
    return {"policies": [pol_svc.policy_card(x, p.user.locale, claims_open=counts.get(x.id, 0)) for x in pols], "renewals": pol_svc.renewal_reminders(pols, lang=p.user.locale)}


@router.get("/insurance-types")
def insurance_types():
    return {"types": [{"code": k, **v} for k, v in INSURANCE_TYPES.items()]}


@router.get("/insurers")
def insurers(db: Session = Depends(get_db)):
    orgs = db.execute(select(Organization).order_by(Organization.name)).scalars().all()
    return {"insurers": [{"id": o.id, "code": o.code, "name": o.name, "name_zh": o.name_zh, "country": o.country, "integration_mode": o.integration_mode} for o in orgs]}


def match_insurer(db: Session, provider_name: str | None) -> Organization | None:
    if not provider_name:
        return None
    needle = provider_name.lower()
    for o in db.execute(select(Organization)).scalars():
        names = [o.name.lower(), (o.name_zh or "").lower(), o.code.lower()]
        first = o.name.lower().split(" (")[0]
        if any(n and (n in needle or needle in n) for n in names) or (first and first in needle):
            return o
    return None


@router.get("/{policy_id}")
def get_policy(policy_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    policy = customer_policy_or_404(db, p, policy_id)
    clauses = db.execute(select(PolicyClause).where(PolicyClause.policy_id == policy.id).order_by(PolicyClause.position)).scalars().all()
    org = db.get(Organization, policy.insurer_org_id) if policy.insurer_org_id else None
    claims = db.execute(select(Claim).where(Claim.policy_id == policy.id).order_by(Claim.created_at.desc())).scalars().all()
    return {
        "policy": pol_svc.policy_card(policy, p.user.locale),
        "insurer": {"name": org.name, "claims_email": org.claims_email, "integration_mode": org.integration_mode} if org else None,
        "clauses": [pol_svc.clause_dict(c, policy.policy_number) for c in clauses],
        "claims": [pol_svc.claim_summary(c, p.user.locale) for c in claims],
        "timeline": pol_svc.timeline_for_policy(db, policy),
    }


class PolicyPatch(BaseModel):
    provider_name: str | None = Field(None, max_length=120)
    product_name: str | None = Field(None, max_length=160)
    insurance_type: str | None = None
    policy_number: str | None = Field(None, max_length=60)
    start_date: date | None = None
    end_date: date | None = None
    premium_amount: float | None = Field(None, ge=0)
    premium_frequency: str | None = Field(None, pattern="^(annual|monthly)$")
    excess_amount: float | None = Field(None, ge=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    insurer_org_id: str | None = Field(None, description="Connect the policy to an insurer on the platform; empty string clears it")
    confirm: bool = Field(False, description="Confirm extracted details and activate the policy")


@router.patch("/{policy_id}")
def patch_policy(policy_id: str, body: PolicyPatch, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    policy = customer_policy_or_404(db, p, policy_id)
    if body.insurance_type and body.insurance_type not in INSURANCE_TYPES:
        raise HTTPException(status_code=400, detail="Unknown insurance type")
    changed = []
    for k, v in body.model_dump(exclude={"confirm", "insurer_org_id"}, exclude_none=True).items():
        setattr(policy, k, v)
        changed.append(k)
    if body.insurer_org_id is not None:
        if body.insurer_org_id == "":
            policy.insurer_org_id = None
        else:
            org = db.get(Organization, body.insurer_org_id)
            if not org:
                raise HTTPException(status_code=400, detail="Unknown insurer")
            policy.insurer_org_id = org.id
        changed.append("insurer_org_id")
        for c in db.execute(select(Claim).where(Claim.policy_id == policy.id, Claim.status.in_(["draft", "information_required", "ready_for_review", "submission_failed"]))).scalars():
            c.insurer_org_id = policy.insurer_org_id
    if body.confirm and policy.status == PolicyStatus.PENDING_REVIEW:
        policy.status = PolicyStatus.ACTIVE if policy.end_date >= date.today() else PolicyStatus.EXPIRED
        changed.append("status")
    if policy.start_date > policy.end_date:
        raise HTTPException(status_code=400, detail="Start date is after end date")
    db.commit()
    audit.record(db, actor_type="customer", actor_id=p.id, action="policy.update", target_type="policy", target_id=policy.id, details={"fields": changed})
    for c in db.execute(select(Claim).where(Claim.policy_id == policy.id, Claim.status.in_(["draft", "information_required", "ready_for_review"]))).scalars():
        from ..services import claims as claim_svc

        claim_svc.refresh_claim_analysis(db, c)
    db.commit()
    return {"policy": pol_svc.policy_card(policy, p.user.locale)}


@router.delete("/{policy_id}")
def delete_policy(policy_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    policy = customer_policy_or_404(db, p, policy_id)
    if db.execute(select(Claim.id).where(Claim.policy_id == policy.id)).first():
        raise HTTPException(status_code=400, detail="This policy has claims linked to it and cannot be removed")
    db.delete(policy)
    db.commit()
    audit.record(db, actor_type="customer", actor_id=p.id, action="policy.delete", target_type="policy", target_id=policy_id)
    return {"ok": True}


class ClausePatch(BaseModel):
    title: str | None = Field(None, max_length=200)
    text: str | None = Field(None, max_length=6000)
    kind: str | None = Field(None, pattern="^(coverage|exclusion|condition|limit|definition|claims_procedure|general)$")
    tags: list[str] | None = None
    amount: float | None = None


@router.patch("/{policy_id}/clauses/{clause_id}")
def patch_clause(policy_id: str, clause_id: str, body: ClausePatch, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    policy = customer_policy_or_404(db, p, policy_id)
    clause = db.get(PolicyClause, clause_id)
    if not clause or clause.policy_id != policy.id:
        raise HTTPException(status_code=404, detail="Clause not found")
    if policy.source != "uploaded":
        raise HTTPException(status_code=400, detail="Only clauses extracted from your own uploads can be corrected")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(clause, k, v)
    clause.confidence = 1.0  # corrected by the customer
    db.commit()
    audit.record(db, actor_type="customer", actor_id=p.id, action="clause.correct", target_type="clause", target_id=clause.id, details={"policy_id": policy.id})
    return {"clause": pol_svc.clause_dict(clause, policy.policy_number)}


@router.post("/upload")
def upload_policy(
    file: UploadFile = File(...),
    provider_name: str | None = Form(None),
    product_name: str | None = Form(None),
    insurance_type: str | None = Form(None),
    policy_number: str | None = Form(None),
    start_date: date | None = Form(None),
    end_date: date | None = Form(None),
    premium_amount: float | None = Form(None),
    excess_amount: float | None = Form(None),
    p: Principal = Depends(require_customer),
    db: Session = Depends(get_db),
):
    doc = store_upload(db, owner_id=p.id, uploader_id=p.id, upload=file, doc_type="policy_wording")
    text = doc.extracted_text or ""
    meta = guess_metadata(text, doc.filename) if text else {"insurance_type_guess": insurance_type or "home", "language": p.user.locale}
    clauses = segment_clauses(text) if text else []
    itype = insurance_type if insurance_type in INSURANCE_TYPES else meta.get("insurance_type_guess", "home")
    lang = meta.get("language") or detect_language(text or "en")
    today = date.today()
    matched_org = match_insurer(db, provider_name or meta.get("provider_name"))
    policy = Policy(
        user_id=p.id,
        insurer_org_id=matched_org.id if matched_org else None,
        provider_name=(provider_name or meta.get("provider_name") or "Unknown provider")[:120],
        product_name=(product_name or doc.filename.rsplit(".", 1)[0])[:160],
        insurance_type=itype,
        policy_number=(policy_number or meta.get("policy_number") or f"UPL-{doc.id[:8].upper()}")[:60],
        language=lang,
        country="CN" if lang == "zh" else "GB",
        currency="CNY" if lang == "zh" else "GBP",
        start_date=start_date or today,
        end_date=end_date or today.replace(year=today.year + 1),
        premium_amount=premium_amount,
        premium_frequency="annual" if premium_amount else None,
        excess_amount=excess_amount,
        status=PolicyStatus.PENDING_REVIEW,
        source="uploaded",
        document_id=doc.id,
        summary={"uploaded": True, "extraction": meta, "clauses_found": len(clauses), "injection_flags": len(doc.injection_flags or [])},
        extraction_confidence=0.65 if clauses else 0.2,
        is_demo_data=False,
    )
    db.add(policy)
    db.flush()
    doc.policy_id = policy.id
    for i, c in enumerate(clauses):
        db.add(PolicyClause(policy_id=policy.id, clause_ref=str(c["ref"])[:30], section=c["section"][:160], title=c["title"][:200], text=c["text"], language=c.get("language", lang), kind=c["kind"], tags=c["tags"], amount=c.get("amount"), position=i, page=c.get("page"), confidence=c.get("confidence", 0.65)))
    audit.record(db, actor_type="customer", actor_id=p.id, action="policy.upload", target_type="policy", target_id=policy.id, details={"clauses": len(clauses), "injection_flags": len(doc.injection_flags or []), "document_id": doc.id}, commit=False)
    db.commit()
    return {
        "policy": pol_svc.policy_card(policy, p.user.locale),
        "extraction": {"metadata_guess": meta, "clauses_found": len(clauses), "text_extracted": bool(text), "injection_flags": doc.injection_flags, "matched_insurer": ({"id": matched_org.id, "name": matched_org.name} if matched_org else None), "note": "Review the extracted details, correct anything wrong, then confirm to activate the policy."},
        "clauses": [{"clause_ref": c["ref"], "title": c["title"], "kind": c["kind"], "tags": c["tags"], "confidence": c.get("confidence", 0.65)} for c in clauses[:40]],
    }
