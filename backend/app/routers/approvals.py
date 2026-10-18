from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Principal, require_customer
from ..services import approvals as approval_svc

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class DecisionIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@router.get("")
def pending(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    return {"approvals": [approval_svc.serialize(a) for a in approval_svc.pending_for_user(db, p.user)]}


@router.get("/{approval_id}")
def get_one(approval_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    try:
        a = approval_svc.get_owned(db, p.user, approval_id)
    except approval_svc.ApprovalError:
        raise HTTPException(status_code=404, detail="Not found")
    return {"approval": approval_svc.serialize(a)}


@router.post("/{approval_id}/approve")
def approve(approval_id: str, body: DecisionIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    try:
        a = approval_svc.decide(db, p.user, approval_id, body.token, approve=True)
    except approval_svc.ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"approval": approval_svc.serialize(a)}


@router.post("/{approval_id}/reject")
def reject(approval_id: str, body: DecisionIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    try:
        a = approval_svc.decide(db, p.user, approval_id, body.token, approve=False)
    except approval_svc.ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"approval": approval_svc.serialize(a)}
