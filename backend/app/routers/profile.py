from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import Principal, get_principal, require_customer
from ..models import AgentRun, Claim, Communication, CustomerProfile, Document, GapAnalysis, Notification, Policy, utcnow
from ..services import policies as pol_svc

router = APIRouter(prefix="/api", tags=["profile"])


class ProfileIn(BaseModel):
    country: str = Field("GB", min_length=2, max_length=2)
    city: str | None = Field(None, max_length=80)
    currency: str = Field("GBP", min_length=3, max_length=3)
    housing: str | None = Field(None, pattern="^(owner|renter|living_with_family)$")
    household: dict[str, Any] = Field(default_factory=dict)
    assets: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    activities: list[dict[str, Any]] = Field(default_factory=list, max_length=40)
    concerns: list[str] = Field(default_factory=list, max_length=20)
    preferences: dict[str, Any] = Field(default_factory=dict)
    consents: dict[str, Any] = Field(default_factory=dict)


def _profile_out(prof: CustomerProfile | None) -> dict | None:
    if not prof:
        return None
    return {"country": prof.country, "city": prof.city, "currency": prof.currency, "housing": prof.housing, "household": prof.household, "assets": prof.assets, "activities": prof.activities, "concerns": prof.concerns, "preferences": prof.preferences, "consents": prof.consents, "updated_at": prof.updated_at.isoformat()}


@router.get("/profile")
def get_profile(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    return {"profile": _profile_out(db.get(CustomerProfile, p.id))}


@router.put("/profile")
def put_profile(body: ProfileIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    prof = db.get(CustomerProfile, p.id)
    if not prof:
        prof = CustomerProfile(user_id=p.id)
        db.add(prof)
    for k, v in body.model_dump().items():
        setattr(prof, k, v)
    if body.preferences.get("language") in {"en", "zh"}:
        p.user.locale = body.preferences["language"]
    db.commit()
    audit.record(db, actor_type="customer", actor_id=p.id, action="profile.update", target_type="profile", target_id=p.id, details={"assets": len(body.assets), "activities": len(body.activities)})
    return {"profile": _profile_out(prof)}


@router.get("/overview")
def overview(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    return pol_svc.overview(db, p.user)


@router.get("/notifications")
def notifications(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(select(Notification).where(Notification.user_id == p.id).order_by(Notification.created_at.desc()).limit(50)).scalars().all()
    return {"notifications": [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "link": n.link, "read_at": n.read_at.isoformat() if n.read_at else None, "created_at": n.created_at.isoformat()} for n in rows], "unread": sum(1 for n in rows if not n.read_at)}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    n = db.get(Notification, notification_id)
    if not n or n.user_id != p.id:
        raise HTTPException(status_code=404, detail="Not found")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def read_all(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    for n in db.execute(select(Notification).where(Notification.user_id == p.id, Notification.read_at.is_(None))).scalars():
        n.read_at = utcnow()
    db.commit()
    return {"ok": True}


@router.get("/export")
def export_my_data(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    """Data portability: everything held about the customer, as JSON."""
    prof = db.get(CustomerProfile, p.id)
    policies = db.execute(select(Policy).where(Policy.user_id == p.id)).scalars().all()
    claims = db.execute(select(Claim).where(Claim.user_id == p.id)).scalars().all()
    docs = db.execute(select(Document).where(Document.owner_user_id == p.id)).scalars().all()
    comms = db.execute(select(Communication).where(Communication.user_id == p.id)).scalars().all()
    gaps = db.execute(select(GapAnalysis).where(GapAnalysis.user_id == p.id)).scalars().all()
    runs = db.execute(select(AgentRun).where(AgentRun.user_id == p.id)).scalars().all()
    audit.record(db, actor_type="customer", actor_id=p.id, action="data.export", target_type="user", target_id=p.id)
    return {
        "exported_at": utcnow().isoformat(),
        "user": {"id": p.id, "email": p.user.email, "full_name": p.user.full_name, "locale": p.user.locale},
        "profile": _profile_out(prof),
        "policies": [pol_svc.policy_card(x) for x in policies],
        "claims": [pol_svc.claim_summary(c) | {"description": c.description, "checklist": c.checklist, "completeness": c.completeness} for c in claims],
        "documents": [{"id": d.id, "filename": d.filename, "doc_type": d.doc_type, "sha256": d.sha256, "uploaded_at": d.created_at.isoformat()} for d in docs],
        "communications": [{"id": m.id, "direction": m.direction, "subject": m.subject, "body": m.body, "status": m.status, "created_at": m.created_at.isoformat()} for m in comms],
        "gap_analyses": [{"id": g.id, "created_at": g.created_at.isoformat(), "findings": g.findings} for g in gaps],
        "assistant_runs": [{"id": r.id, "intent": r.intent, "status": r.status, "started_at": r.started_at.isoformat()} for r in runs],
    }


@router.post("/account/close")
def close_account(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    """Deactivates the account and records a deletion request. Records that
    an insurer must retain for a claim are kept under the insurer's
    retention policy; everything else is scheduled for deletion."""
    p.user.is_active = False
    audit.record(db, actor_type="customer", actor_id=p.id, action="account.close_requested", target_type="user", target_id=p.id, commit=False)
    db.commit()
    return {"ok": True, "note": "Account deactivated. Deletion runs after the retention check; claim records required by insurers are retained under their policy."}
