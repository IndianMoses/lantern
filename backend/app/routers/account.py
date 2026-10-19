"""Consent decisions, explicit record sharing and answer feedback."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import Principal, get_principal, require_customer
from ..models import AgentRun, Claim, Feedback, Message, Policy, RecordShare, Roles, User, utcnow
from ..services import bank as bank_svc

router = APIRouter(prefix="/api", tags=["account"])


@router.get("/consents")
def consents(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    return {"consents": list(bank_svc.current_consents(db, p.id).values())}


class ConsentIn(BaseModel):
    purpose: str = Field(max_length=60)
    granted: bool
    note: str | None = Field(None, max_length=300)


@router.post("/consents")
def set_consent(body: ConsentIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    try:
        row = bank_svc.record_consent(db, p.id, body.purpose, body.granted, note=body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    audit.record(db, actor_type="customer", actor_id=p.id, action="consent.changed", target_type="consent", target_id=row.id, details={"purpose": body.purpose, "granted": body.granted})
    # the bank's consent manager is kept in step (mock adapter; no live connection)
    bank_svc.adapters_for(None).sync(p.id, {body.purpose: body.granted})
    return {"consents": list(bank_svc.current_consents(db, p.id).values())}


# ------------------------------------------------------------------ shares ---


class ShareIn(BaseModel):
    grantee_email: str = Field(max_length=200)
    record_type: str = Field(pattern="^(policy|claim)$")
    record_id: str
    permissions: list[str] = Field(default_factory=lambda: ["view"])


@router.get("/shares")
def list_shares(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    given = db.execute(select(RecordShare).where(RecordShare.owner_user_id == p.id, RecordShare.revoked_at.is_(None))).scalars().all()
    received = db.execute(select(RecordShare).where(RecordShare.grantee_user_id == p.id, RecordShare.revoked_at.is_(None))).scalars().all()

    def out(r: RecordShare) -> dict:
        other = db.get(User, r.grantee_user_id if r.owner_user_id == p.id else r.owner_user_id)
        label = None
        if r.record_type == "policy":
            pol = db.get(Policy, r.record_id)
            label = pol.policy_number if pol else None
        else:
            cl = db.get(Claim, r.record_id)
            label = cl.reference if cl else None
        return {"id": r.id, "record_type": r.record_type, "record_id": r.record_id, "record_label": label, "permissions": r.permissions, "with": {"name": other.full_name, "email": other.email, "role": other.role} if other else None, "created_at": r.created_at.isoformat()}

    return {"given": [out(r) for r in given], "received": [out(r) for r in received]}


@router.post("/shares")
def create_share(body: ShareIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    if set(body.permissions) - {"view", "upload"}:
        raise HTTPException(status_code=400, detail="permissions may be view or upload")
    grantee = db.execute(select(User).where(User.email == body.grantee_email.lower())).scalar_one_or_none()
    if not grantee or grantee.id == p.id:
        raise HTTPException(status_code=404, detail="No account with that email")
    if grantee.role not in {Roles.HOUSEHOLD_MEMBER, Roles.ADVISER, Roles.CUSTOMER}:
        raise HTTPException(status_code=400, detail="Records can be shared with household members, advisers or other customers only")
    record = db.get(Policy, body.record_id) if body.record_type == "policy" else db.get(Claim, body.record_id)
    if not record or record.user_id != p.id:
        raise HTTPException(status_code=404, detail="Record not found")
    existing = db.execute(select(RecordShare).where(RecordShare.owner_user_id == p.id, RecordShare.grantee_user_id == grantee.id, RecordShare.record_type == body.record_type, RecordShare.record_id == body.record_id, RecordShare.revoked_at.is_(None))).scalar_one_or_none()
    if existing:
        existing.permissions = body.permissions
        db.commit()
        return {"share": {"id": existing.id}}
    share = RecordShare(owner_user_id=p.id, grantee_user_id=grantee.id, record_type=body.record_type, record_id=body.record_id, permissions=body.permissions)
    db.add(share)
    db.commit()
    audit.record(db, actor_type="customer", actor_id=p.id, action="share.granted", target_type=body.record_type, target_id=body.record_id, details={"grantee": grantee.id, "permissions": body.permissions})
    return {"share": {"id": share.id}}


@router.delete("/shares/{share_id}")
def revoke_share(share_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    share = db.get(RecordShare, share_id)
    if not share or (share.owner_user_id != p.id and share.grantee_user_id != p.id) or share.revoked_at:
        raise HTTPException(status_code=404, detail="Share not found")
    share.revoked_at = utcnow()
    db.commit()
    audit.record(db, actor_type=p.role, actor_id=p.id, action="share.revoked", target_type=share.record_type, target_id=share.record_id)
    return {"ok": True}


# ---------------------------------------------------------------- feedback ---


class FeedbackIn(BaseModel):
    message_id: str | None = None
    run_id: str | None = None
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(None, max_length=500)


@router.post("/feedback")
def feedback(body: FeedbackIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if body.rating == 0:
        raise HTTPException(status_code=400, detail="rating must be 1 or -1")
    if body.message_id:
        m = db.get(Message, body.message_id)
        run = db.get(AgentRun, m.run_id) if m and m.run_id else None
        if not m or not run or run.user_id != p.id:
            raise HTTPException(status_code=404, detail="Message not found")
    db.add(Feedback(user_id=p.id, message_id=body.message_id, run_id=body.run_id, rating=body.rating, comment=(body.comment or "").strip() or None))
    db.commit()
    return {"ok": True}
