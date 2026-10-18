from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, require_customer
from ..models import Claim, Communication, utcnow
from ..services import approvals as approval_svc

router = APIRouter(prefix="/api/communications", tags=["communications"])


def comm_out(c: Communication, claim_ref: str | None = None) -> dict:
    return {"id": c.id, "claim_id": c.claim_id, "claim_reference": claim_ref, "policy_id": c.policy_id, "direction": c.direction, "channel": c.channel, "kind": c.kind, "subject": c.subject, "body": c.body, "recipient": c.recipient, "status": c.status, "generated_by": c.generated_by, "sent_at": c.sent_at.isoformat() if c.sent_at else None, "read_at": c.read_at.isoformat() if c.read_at else None, "created_at": c.created_at.isoformat(), "referenced_document_ids": c.referenced_document_ids}


@router.get("")
def inbox(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    rows = db.execute(select(Communication, Claim.reference).outerjoin(Claim, Claim.id == Communication.claim_id).where(Communication.user_id == p.id).order_by(Communication.created_at.desc())).all()
    return {"communications": [comm_out(c, ref) for c, ref in rows], "unread": sum(1 for c, _ in rows if c.direction == "inbound" and not c.read_at)}


class CommPatch(BaseModel):
    subject: str | None = Field(None, max_length=200)
    body: str | None = Field(None, max_length=8000)


@router.patch("/{communication_id}")
def edit_draft(communication_id: str, body: CommPatch, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    c = db.get(Communication, communication_id)
    if not c or c.user_id != p.id:
        raise HTTPException(status_code=404, detail="Not found")
    if c.status != "draft":
        raise HTTPException(status_code=400, detail="Only drafts can be edited")
    if body.subject is not None:
        c.subject = body.subject
    if body.body is not None:
        c.body = body.body
    c.generated_by = "user"
    db.commit()
    return {"communication": comm_out(c)}


@router.post("/{communication_id}/send")
def send(communication_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    c = db.get(Communication, communication_id)
    if not c or c.user_id != p.id:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        created = approval_svc.create_communication_approval(db, p.user, c, actor_type="customer")
    except (approval_svc.ApprovalError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    return {"approval": approval_svc.serialize(created.approval, created.token)}


@router.post("/{communication_id}/read")
def mark_read(communication_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    c = db.get(Communication, communication_id)
    if not c or c.user_id != p.id:
        raise HTTPException(status_code=404, detail="Not found")
    c.read_at = c.read_at or utcnow()
    db.commit()
    return {"ok": True}


@router.delete("/{communication_id}")
def discard_draft(communication_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    c = db.get(Communication, communication_id)
    if not c or c.user_id != p.id:
        raise HTTPException(status_code=404, detail="Not found")
    if c.status != "draft":
        raise HTTPException(status_code=400, detail="Only drafts can be discarded")
    db.delete(c)
    db.commit()
    return {"ok": True}
