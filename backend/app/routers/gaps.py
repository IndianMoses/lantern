from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import Principal, require_customer
from ..models import CustomerProfile, GapAnalysis
from ..services import gap_engine
from ..services import policies as pol_svc

router = APIRouter(prefix="/api/gaps", tags=["gaps"])


def _out(g: GapAnalysis) -> dict:
    return {"id": g.id, "created_at": g.created_at.isoformat(), "findings": g.findings, "summary": g.summary, "inputs_snapshot": g.inputs_snapshot, "disclaimer": gap_engine.analyze.__doc__ and {"en": "Potential issues derived from your profile and policy clauses; not coverage determinations or regulated advice.", "zh": "根据你的资料和保单条款得出的潜在问题，不是保障结论，也不构成受监管的建议。"}}


@router.post("/analyze")
def analyze(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    prof = db.get(CustomerProfile, p.id)
    if not prof:
        raise HTTPException(status_code=400, detail="Complete your profile first")
    result = gap_engine.analyze(prof, pol_svc.policies_with_clauses(db, p.id))
    g = GapAnalysis(user_id=p.id, findings=result["findings"], summary=result["summary"], inputs_snapshot={"assets": prof.assets, "activities": prof.activities, "housing": prof.housing})
    db.add(g)
    db.commit()
    audit.record(db, actor_type="customer", actor_id=p.id, action="gap_analysis.run", target_type="gap_analysis", target_id=g.id, details={"counts": result["summary"]["counts"]})
    return _out(g)


@router.get("/latest")
def latest(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    g = db.execute(select(GapAnalysis).where(GapAnalysis.user_id == p.id).order_by(GapAnalysis.created_at.desc()).limit(1)).scalar_one_or_none()
    return {"analysis": _out(g) if g else None}
