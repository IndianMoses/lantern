"""Coverage gap analysis: jobs with real stage status, persisted findings,
the coverage graph, facts and human review requests."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import get_db
from ..deps import Principal, require_customer
from ..models import CoverageAnalysisJob, CoverageFinding, CustomerProfile, GapAnalysis, ReviewRequest, utcnow
from ..services import analysis as analysis_svc
from ..services import facts as fact_svc
from ..services import graph as graph_svc
from ..services import policies as pol_svc

router = APIRouter(prefix="/api/gaps", tags=["gaps"])


class AnalyzeIn(BaseModel):
    sync: bool = False
    mode: str | None = Field(None, pattern="^(models|deterministic)$")


@router.post("/analyze")
def analyze(body: AnalyzeIn | None = None, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    body = body or AnalyzeIn()
    prof = db.get(CustomerProfile, p.id)
    if not prof:
        raise HTTPException(status_code=400, detail="Complete your profile first")
    mode = body.mode
    if mode == "models" and not settings.models_configured:
        raise HTTPException(status_code=409, detail="No model endpoints are configured; the analysis runs in deterministic mode")
    job = analysis_svc.start(db, p.user, mode=mode, language=p.lang, background=not body.sync and settings.analysis_run_in_background)
    db.refresh(job)
    if job.status == "completed" and job.analysis_id:
        ga = db.get(GapAnalysis, job.analysis_id)
        return {"job": analysis_svc.job_dict(job), "analysis": analysis_svc.analysis_dict(db, ga)}
    if job.status == "failed":
        raise HTTPException(status_code=500, detail=job.error or "analysis failed")
    return {"job": analysis_svc.job_dict(job), "analysis": None}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    job = db.get(CoverageAnalysisJob, job_id)
    if not job or job.user_id != p.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": analysis_svc.job_dict(job)}


@router.get("/latest")
def latest(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    g = db.execute(select(GapAnalysis).where(GapAnalysis.user_id == p.id).order_by(GapAnalysis.created_at.desc()).limit(1)).scalar_one_or_none()
    return {"analysis": analysis_svc.analysis_dict(db, g) if g else None}


@router.get("/history")
def history(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    rows = db.execute(select(GapAnalysis).where(GapAnalysis.user_id == p.id).order_by(GapAnalysis.created_at.desc()).limit(20)).scalars().all()
    return {"analyses": [{"id": g.id, "created_at": g.created_at.isoformat(), "mode": g.mode, "counts": (g.summary or {}).get("counts", {}), "pipeline_version": g.pipeline_version} for g in rows]}


@router.get("/facts")
def facts(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    pols = pol_svc.policies_with_clauses(db, p.id)
    by_id = {pol.id: (pol, {c.id: c for c in cls}) for pol, cls in pols}
    stored = fact_svc.facts_for_policies(db, list(by_id))
    out = []
    for pid, rows in stored.items():
        pol, cmap = by_id[pid]
        for f in rows:
            c = cmap.get(f.clause_id or "")
            out.append(fact_svc.fact_dict(f, pol.policy_number, c.clause_ref if c else None))
    return {"facts": out, "policies": [{"id": pol.id, "number": pol.policy_number, "status": pol.status, "version": pol.version, "effective_from": pol.start_date.isoformat(), "effective_to": pol.end_date.isoformat()} for pol, _ in pols]}


@router.get("/{analysis_id}")
def one(analysis_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    g = db.get(GapAnalysis, analysis_id)
    if not g or g.user_id != p.id:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {"analysis": analysis_svc.analysis_dict(db, g)}


@router.get("/{analysis_id}/graph/{node_key:path}")
def node_relationships(analysis_id: str, node_key: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    g = db.get(GapAnalysis, analysis_id)
    if not g or g.user_id != p.id:
        raise HTTPException(status_code=404, detail="Analysis not found")
    graph = graph_svc.load(db, g.id)
    node = next((n for n in graph["nodes"] if n["key"] == node_key), None)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"node": node, "relationships": graph_svc.neighbours(graph, node_key)}


class ReviewIn(BaseModel):
    reason: str = Field("", max_length=1000)


@router.post("/findings/{finding_id}/review")
def request_review(finding_id: str, body: ReviewIn, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    f = db.get(CoverageFinding, finding_id)
    if not f or f.user_id != p.id:
        raise HTTPException(status_code=404, detail="Finding not found")
    existing = db.execute(select(ReviewRequest).where(ReviewRequest.finding_id == f.id, ReviewRequest.status == "open")).scalar_one_or_none()
    if existing:
        return {"review": {"id": existing.id, "status": existing.status}, "finding_id": f.id, "review_status": f.review_status}
    r = ReviewRequest(user_id=p.id, analysis_id=f.analysis_id, finding_id=f.id, reason=body.reason.strip())
    f.review_status = "requested"
    payload = dict(f.payload or {})
    payload["review_status"] = "requested"
    f.payload = payload
    ga = db.get(GapAnalysis, f.analysis_id)
    if ga:
        ga.findings = [dict(x, review_status="requested") if x.get("id") == f.id else x for x in (ga.findings or [])]
    db.add(r)
    db.commit()
    audit.record(db, actor_type="customer", actor_id=p.id, action="finding.review_requested", target_type="coverage_finding", target_id=f.id, details={"analysis_id": f.analysis_id, "need": f.need})
    return {"review": {"id": r.id, "status": r.status, "created_at": r.created_at.isoformat()}, "finding_id": f.id, "review_status": "requested"}


@router.get("/reviews/open")
def open_reviews(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    rows = db.execute(select(ReviewRequest).where(ReviewRequest.user_id == p.id).order_by(ReviewRequest.created_at.desc())).scalars().all()
    return {"reviews": [{"id": r.id, "finding_id": r.finding_id, "analysis_id": r.analysis_id, "status": r.status, "reason": r.reason, "created_at": r.created_at.isoformat(), "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None, "resolution": r.resolution} for r in rows]}


__all__ = ["router", "utcnow"]
