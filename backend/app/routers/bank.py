"""Bank operations surface: aggregate analytics, integration health, audit.

Purpose limitation in code: a bank administrator sees counts and rates for
the bank's own customers, never conversations, documents, claims or names.
Small cells are suppressed (fewer than ``K_ANON`` customers) and every access
is audit logged with the metrics requested.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import Principal, require_bank_admin
from ..models import AgentRun, AuditEvent, Claim, ClaimStatus, Conversation, CoverageAnalysisJob, CustomerProfile, Feedback, GapAnalysis, Organization, Policy, ReviewRequest, User, UserSession, utcnow
from ..services import bank as bank_svc

router = APIRouter(prefix="/api/bank", tags=["bank"])
K_ANON = 3


def _customers(db: Session, org_id: str) -> list[str]:
    return [uid for (uid,) in db.execute(select(CustomerProfile.user_id).where(CustomerProfile.bank_org_id == org_id))]


def _rate(n: int, d: int) -> float | None:
    return round(n / d, 3) if d else None


def _suppress(value, n: int):
    return value if n >= K_ANON else None


@router.get("/overview")
def overview(p: Principal = Depends(require_bank_admin), db: Session = Depends(get_db)):
    org = db.get(Organization, p.org_id)
    ids = _customers(db, p.org_id)
    n = len(ids)
    now = utcnow()
    since30 = now - timedelta(days=30)
    consented = [uid for uid in ids if bank_svc.allowed(db, uid, "analytics_aggregate")]
    base = consented  # only customers who allowed aggregate analytics are counted
    nb = len(base)
    active30 = db.execute(select(func.count(func.distinct(UserSession.user_id))).where(UserSession.user_id.in_(base), UserSession.last_seen_at >= since30)).scalar() if base else 0
    with_analysis = db.execute(select(func.count(func.distinct(GapAnalysis.user_id))).where(GapAnalysis.user_id.in_(base))).scalar() if base else 0
    policies = db.execute(select(func.count(Policy.id)).where(Policy.user_id.in_(base))).scalar() if base else 0
    active_policies = db.execute(select(func.count(Policy.id)).where(Policy.user_id.in_(base), Policy.status == "active")).scalar() if base else 0
    # potential gaps from each customer's latest analysis
    gaps = 0
    partial = 0
    verified = 0
    findings_total = 0
    for uid in base:
        ga = db.execute(select(GapAnalysis).where(GapAnalysis.user_id == uid).order_by(GapAnalysis.created_at.desc()).limit(1)).scalar_one_or_none()
        if ga:
            c = (ga.summary or {}).get("counts", {})
            gaps += c.get("gap", 0)
            partial += c.get("partial", 0)
            verified += c.get("verified", 0)
            findings_total += len(ga.findings or [])
    open_claims = db.execute(select(func.count(Claim.id)).where(Claim.user_id.in_(base), Claim.status.in_(list(ClaimStatus.PRE_SUBMISSION | ClaimStatus.OPEN_WITH_INSURER)))).scalar() if base else 0
    customers_with_tasks = db.execute(select(func.count(func.distinct(Claim.user_id))).where(Claim.user_id.in_(base), Claim.status.in_(list(ClaimStatus.PRE_SUBMISSION | ClaimStatus.OPEN_WITH_INSURER)))).scalar() if base else 0
    conversations = db.execute(select(func.count(Conversation.id)).where(Conversation.user_id.in_(base), Conversation.surface == "customer")).scalar() if base else 0
    runs = db.execute(select(AgentRun).where(AgentRun.user_id.in_(base))).scalars().all() if base else []
    completed = sum(1 for r in runs if r.status in {"completed", "awaiting_approval"})
    failed = sum(1 for r in runs if r.status == "failed")
    fallbacks = sum(1 for r in runs if "->" in (r.provider or ""))
    cit_valid = sum((r.grounding or {}).get("citations_valid", 0) for r in runs)
    cit_unv = sum((r.grounding or {}).get("citations_unverified", 0) for r in runs)
    reviews = db.execute(select(func.count(ReviewRequest.id)).where(ReviewRequest.user_id.in_(base))).scalar() if base else 0
    fb = db.execute(select(Feedback.rating).where(Feedback.user_id.in_(base))).scalars().all() if base else []
    jobs = db.execute(select(CoverageAnalysisJob).where(CoverageAnalysisJob.user_id.in_(base))).scalars().all() if base else []
    model_runs = sum(1 for j in jobs if j.mode == "models")
    mixed_runs = sum(1 for j in jobs if j.mode == "mixed")
    metrics = {
        "customers_total": {"value": n, "definition": "Customers of this bank registered on the platform"},
        "customers_counted": {"value": nb, "definition": "Customers who allowed aggregate analytics; all rates below use this base"},
        "active_30d": {"value": _suppress(active30, nb), "definition": "Counted customers with a session in the last 30 days"},
        "engagement_rate_30d": {"value": _suppress(_rate(active30, nb), nb), "definition": "active_30d / customers_counted"},
        "coverage_analysis_completion": {"value": _suppress(_rate(with_analysis, nb), nb), "definition": "Counted customers with at least one completed coverage analysis"},
        "policies_organised": {"value": _suppress(policies, nb), "definition": "Policies recorded by counted customers (all statuses)"},
        "active_policies": {"value": _suppress(active_policies, nb), "definition": "Policies with status active"},
        "potential_gaps_identified": {"value": _suppress(gaps + partial, nb), "definition": "Gap and partial findings in each counted customer's latest analysis"},
        "customers_with_open_tasks": {"value": _suppress(customers_with_tasks, nb), "definition": "Counted customers with a claim in preparation or open with an insurer"},
        "open_claims": {"value": _suppress(open_claims, nb), "definition": "Claims in preparation or open with an insurer"},
        "support_interactions": {"value": _suppress(conversations, nb), "definition": "Assistant conversations started by counted customers"},
        "assistant_runs": {"value": _suppress(len(runs), nb), "definition": "Assistant turns executed"},
        "assistant_resolution_rate": {"value": _suppress(_rate(completed, len(runs)), nb), "definition": "Runs completed without error / runs"},
        "assistant_escalation_rate": {"value": _suppress(_rate(reviews, max(1, with_analysis)), nb), "definition": "Human review requests / customers with an analysis"},
        "assistant_failure_rate": {"value": _suppress(_rate(failed, len(runs)), nb), "definition": "Runs that ended in error / runs"},
        "citation_verification_rate": {"value": _suppress(_rate(cit_valid, cit_valid + cit_unv), nb), "definition": "Citations verified against retrieved clauses / all citations"},
        "provider_fallbacks": {"value": _suppress(fallbacks, nb), "definition": "Runs where the model provider failed and the offline planner answered"},
        "finding_verification_rate": {"value": _suppress(_rate(verified, findings_total), nb), "definition": "Findings verified by the evidence layers / findings in latest analyses"},
        "analysis_runs_models": {"value": _suppress(model_runs, nb), "definition": "Coverage analyses run with all three models"},
        "analysis_runs_mixed": {"value": _suppress(mixed_runs, nb), "definition": "Analyses where a model fell back to rules"},
        "satisfaction": {"value": _suppress(_rate(sum(1 for r in fb if r > 0), len(fb)), nb) if fb else None, "definition": "Share of answers rated helpful (thumbs up / all ratings)", "responses": len(fb) if nb >= K_ANON else None},
    }
    audit.record(db, actor_type=p.role, actor_id=p.id, action="bank.analytics_viewed", target_type="organization", target_id=p.org_id, details={"metrics": sorted(metrics)})
    # the same aggregate (and nothing more) is what the bank's reporting sink would receive; mock adapter, in process only
    bank_svc.adapters_for(org).publish({k: v["value"] for k, v in metrics.items()})
    return {"organization": {"id": org.id, "name": org.name, "name_zh": org.name_zh, "kind": org.kind}, "metrics": metrics, "k_anonymity": K_ANON, "note": "Aggregate statistics only. This surface never exposes conversations, documents, claims or identifiable customers; cells below the suppression threshold are shown as not available.", "generated_at": utcnow().isoformat()}


@router.get("/integration")
def integration(p: Principal = Depends(require_bank_admin), db: Session = Depends(get_db)):
    org = db.get(Organization, p.org_id)
    ids = _customers(db, p.org_id)
    health = bank_svc.integration_health(org)
    stats = bank_svc.consent_statistics(db, ids)
    audit.record(db, actor_type=p.role, actor_id=p.id, action="bank.integration_viewed", target_type="organization", target_id=p.org_id)
    return {"interfaces": health, "consents": [{"purpose": k, "label": bank_svc.PURPOSES[k], **v} for k, v in stats.items()], "customers": len(ids), "data_boundary": {"ingested": ["profile fields the customer entered", "policies and claims the customer created or uploaded", "consent decisions"], "never_ingested": ["transactions", "balances", "credit data", "unrelated personal information"]}}


@router.get("/audit")
def audit_view(limit: int = Query(100, le=500), p: Principal = Depends(require_bank_admin), db: Session = Depends(get_db)):
    """The bank's own access trail: what bank staff looked at, plus platform events about the integration."""
    staff = [uid for (uid,) in db.execute(select(User.id).where(User.org_id == p.org_id))]
    rows = db.execute(select(AuditEvent).where((AuditEvent.actor_id.in_(staff)) | (AuditEvent.action.like("bank.%"))).order_by(AuditEvent.seq.desc()).limit(limit)).scalars().all()
    chain = audit.verify_chain(db)
    return {"events": [{"seq": a.seq, "action": a.action, "actor_type": a.actor_type, "actor_id": a.actor_id, "target_type": a.target_type, "outcome": a.outcome, "created_at": a.created_at.isoformat(), "hash": a.hash, "details": a.details} for a in rows], "chain": {"ok": chain["ok"], "checked": chain.get("checked"), "head": chain.get("head")}}


@router.get("/customers/{customer_id}")
def no_customer_access(customer_id: str, p: Principal = Depends(require_bank_admin)):
    """Deliberately not available: bank staff do not get identifiable customer records."""
    raise HTTPException(status_code=403, detail="Bank staff do not have access to identifiable customer records on this platform")
