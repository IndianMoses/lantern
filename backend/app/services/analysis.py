"""Coverage analysis pipeline (the ten stage process from the brief).

 1 customer context   only the profile fields and active policies the analysis needs
 2 extraction         typed facts for every policy (services.facts)
 3 graph              preliminary coverage graph
 4 analysts           Model A and Model B analyse the same evidence independently
 5 challenge          Model B reviews A's findings after forming its own view
 6 judge              Model C adjudicates against the evidence
 7 verification       deterministic layers A to F on every surviving finding
 8 calculation        illustrative figures only where the terms support them
 9 report             prioritised findings, graph and persistence
10 escalation         unresolved or high impact items are marked for a person

Each stage writes its real status to the job record so the interface shows
what actually happened, including fallbacks. A model failure never produces a
finding: the role falls back to the rule based implementation and the
analysis is labelled ``mixed`` instead of ``models``.
"""
from __future__ import annotations

import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..agent.analysts import AnalystOutput, CandidateFinding, ChallengeOutput, JudgeOutput, OpenAICompatibleAnalyst, RuleBasedAnalyst, build_package
from ..agent.providers.base import ProviderError
from ..config import settings
from ..db import SessionLocal
from ..models import Claim, CoverageAnalysisJob, CoverageFinding, CustomerProfile, GapAnalysis, Policy, PolicyClause, PolicyFact, User, utcnow
from . import calculator, facts as fact_svc, graph as graph_svc, prioritisation, verification
from . import policies as pol_svc

log = logging.getLogger("lantern.analysis")

PIPELINE_VERSION = "2.0"
STAGE_KEYS = ["customer_context", "extraction", "graph", "analysts", "challenge", "judge", "verification", "calculation", "report"]
DISCLAIMER = {
    "en": "These are potential issues derived from your profile, the typed facts of your policies and their clauses. They are not coverage determinations or regulated advice; confirm with your insurer before relying on them.",
    "zh": "以上是根据你的资料、保单结构化事实及条款得出的潜在问题，不是保险公司的保障结论，也不构成受监管的建议；依赖前请向保险公司确认。",
}


class _Stages:
    def __init__(self, db: Session, job: CoverageAnalysisJob | None):
        self.db = db
        self.job = job
        self.stages = [{"key": k, "status": "pending", "started_at": None, "ended_at": None, "detail": {}} for k in STAGE_KEYS]
        self._save()

    def _save(self) -> None:
        if self.job is not None:
            self.job.stages = [dict(s) for s in self.stages]
            self.job.updated_at = utcnow()
            self.db.commit()

    def start(self, key: str) -> None:
        s = next(x for x in self.stages if x["key"] == key)
        s["status"], s["started_at"] = "running", utcnow().isoformat()
        self._save()

    def done(self, key: str, **detail) -> None:
        s = next(x for x in self.stages if x["key"] == key)
        s["status"], s["ended_at"] = "done", utcnow().isoformat()
        s["detail"].update(detail)
        self._save()

    def fail(self, key: str, error: str) -> None:
        s = next(x for x in self.stages if x["key"] == key)
        s["status"], s["ended_at"] = "failed", utcnow().isoformat()
        s["detail"]["error"] = error[:500]
        self._save()


def _providers(mode: str, profile: CustomerProfile, policies, facts, today: date):
    rules = {r: RuleBasedAnalyst(r, profile=profile, policies=policies, facts=facts, today=today) for r in ("A", "B", "C")}
    if mode != "models":
        return rules, {r: {"provider": "rules", "model": rules[r].model_id, "fallback": False} for r in rules}
    cfg = {"A": (settings.model_a_url, settings.model_a_name, settings.model_a_api_key), "B": (settings.model_b_url, settings.model_b_name, settings.model_b_api_key), "C": (settings.model_c_url, settings.model_c_name, settings.model_c_api_key)}
    out, meta = {}, {}
    for r, (url, name, key) in cfg.items():
        out[r] = OpenAICompatibleAnalyst(r, base_url=url, model=name, api_key=key, timeout=settings.model_timeout_seconds, max_retries=settings.model_max_retries, max_tokens=settings.model_max_tokens)  # type: ignore[arg-type]
        meta[r] = {"provider": "openai_compatible", "model": name, "endpoint": url, "fallback": False}
    return out, meta


def _with_fallback(role: str, fn, fallback_fn, meta: dict):
    try:
        return fn()
    except ProviderError as exc:
        log.warning("Model %s failed (%s); falling back to rules", role, exc)
        meta[role].update({"fallback": True, "error": str(exc)[:300], "provider": "rules"})
        return fallback_fn()


def _candidate_to_dict(f: CandidateFinding, clause_index: dict[str, tuple[Policy, PolicyClause]]) -> dict:
    if f.legacy:
        d = dict(f.legacy)
    else:
        evidence = []
        for cid in f.clause_ids:
            hit = clause_index.get(cid)
            if hit:
                p, c = hit
                evidence.append({"clause_id": c.id, "policy_number": p.policy_number, "clause_ref": c.clause_ref, "citation": f"{p.policy_number} §{c.clause_ref}", "title": c.title, "kind": c.kind, "amount": c.amount, "note": ""})
        d = {"need": f.need, "label": f.title or {"en": f.need, "zh": f.need}, "status": f.status, "severity": f.severity, "explanation": f.statement, "suggested_action": f.suggested_action, "triggered_by": f.triggered_by, "evidence": evidence, "policies": sorted(set(f.policy_numbers) | {e["policy_number"] for e in evidence})}
    d["key"] = f.key
    d["kind"] = f.kind
    d["assumptions"] = list(f.assumptions)
    d["missing_information"] = list(f.missing_information)
    d["scenario"] = f.scenario.model_dump() if f.scenario else None
    d["models"] = {"proposed_by": list(f.proposed_by), "rationale": f.rationale}
    d["derived_figures"] = list(getattr(f, "_derived", []) or [])
    return d


def _illustrate(d: dict, profile: CustomerProfile, by_number: dict[str, Policy], facts: dict[str, list[PolicyFact]], clause_index: dict[str, tuple[Policy, PolicyClause]]) -> dict | None:
    """Illustrative payable / uncovered figures for a partial finding when the
    limit, the excess and the declared value are all stated facts."""
    if d.get("status") != "partial" or not d.get("evidence"):
        return None
    trig = (d.get("triggered_by") or "").lower()
    asset = next((a for a in (profile.assets or []) if (a.get("label") or "").lower() and ((a.get("label") or "").lower() in trig or trig in (a.get("label") or "").lower())), None)
    value = float(asset.get("value") or 0) if asset else 0.0
    if value <= 0:
        return None
    limit_clause = None
    for e in d["evidence"]:
        hit = clause_index.get(e["clause_id"])
        if hit and hit[1].amount and hit[1].kind in {"coverage", "definition", "limit"}:
            limit_clause = hit
            break
    if not limit_clause:
        return None
    p, c = limit_clause
    limit_fact = next((f for f in facts.get(p.id, []) if f.clause_id == c.id and f.fact_type in {"limit", "sub_limit", "coverage", "definition"} and f.value == c.amount), None)
    excess_fact = next((f for f in facts.get(p.id, []) if f.fact_type == "excess" and f.subject == "general" and f.value is not None), None)
    validated = bool(limit_fact and excess_fact and limit_fact.status == "known" and excess_fact.status == "known")
    rule, problems = calculator.validate_rule({
        "rule_id": f"rule:{d['need']}:{p.policy_number}", "policy_id": p.id, "policy_number": p.policy_number, "covered_event": d["need"], "subject": d["need"], "currency": p.currency,
        "limit": c.amount, "excess": excess_fact.value if excess_fact else None, "conditions": [], "exclusions": [], "evidence_clause_ids": [c.id],
        "evidence_fact_ids": [x.id for x in (limit_fact, excess_fact) if x], "effective_from": p.start_date, "effective_to": p.end_date,
        "calculation_method": "limit_then_excess", "interpretation_status": "validated" if validated else "needs_review",
    })
    if rule is None:
        return {"status": "rejected", "problems": problems}
    if problems and rule.interpretation_status == "validated":
        rule.interpretation_status = "needs_review"
    if rule.interpretation_status != "validated":
        return {"status": "unresolved", "rule_id": rule.rule_id, "unresolved_conditions": problems or ["limit or excess not confirmed as stated facts"], "label": {"en": "Not calculated: the terms needed are not all stated facts.", "zh": "未计算：所需条款不全是明确写明的事实。"}}
    try:
        res = calculator.calculate(rule, value)
    except (calculator.UnsupportedCalculation, calculator.RuleNotValidated) as exc:
        return {"status": "rejected", "problems": [str(exc)]}
    out = res.to_dict()
    out["rule"] = rule.model_dump(mode="json")
    out["basis"] = {"en": f"Declared value {value:,.0f} {p.currency} against the stated limit {c.amount:,.0f} {p.currency} and excess {rule.excess:,.0f} {p.currency}.", "zh": f"申报价值 {value:,.0f} {p.currency}，对照条款限额 {c.amount:,.0f} {p.currency} 和免赔额 {rule.excess:,.0f} {p.currency}。"}
    return out


def run(db: Session, user: User, *, mode: str | None = None, run_id: str | None = None, language: str = "en", job: CoverageAnalysisJob | None = None, today: date | None = None) -> GapAnalysis:
    today = today or date.today()
    mode = mode or settings.effective_analysis_mode
    stages = _Stages(db, job)
    if job is not None:
        job.status, job.mode = "running", mode
        db.commit()

    # 1 customer context
    stages.start("customer_context")
    profile = db.get(CustomerProfile, user.id)
    if not profile:
        stages.fail("customer_context", "profile missing")
        raise ValueError("Complete your profile first")
    all_policies = pol_svc.policies_with_clauses(db, user.id)
    active = [(p, c) for p, c in all_policies if p.status == "active"]
    claims = list(db.execute(select(Claim).where(Claim.user_id == user.id)).scalars())
    stages.done("customer_context", assets=len(profile.assets or []), activities=len(profile.activities or []), policies_active=len(active), policies_total=len(all_policies), fields_used=["housing", "household", "assets", "activities", "concerns", "currency"])

    # 2 extraction
    stages.start("extraction")
    facts: dict[str, list[PolicyFact]] = {}
    for p, clauses in all_policies:
        facts[p.id] = fact_svc.refresh_policy_facts(db, p, clauses)
    n_facts = sum(len(v) for v in facts.values())
    stages.done("extraction", facts=n_facts, not_found=sum(1 for v in facts.values() for f in v if f.status == "not_found"), conflicting=sum(1 for v in facts.values() for f in v if f.status == "conflicting"), extractor=f"{fact_svc.EXTRACTOR}@{fact_svc.EXTRACTOR_VERSION}")

    # 3 preliminary graph (nodes only; edges with findings come at the end)
    stages.start("graph")
    clause_index = {c.id: (p, c) for p, cls in all_policies for c in cls}
    by_number = {p.policy_number: p for p, _ in all_policies}
    stages.done("graph", clauses=len(clause_index), policies=len(all_policies))

    # 4 independent analysts
    stages.start("analysts")
    providers, meta = _providers(mode, profile, all_policies, facts, today)
    rules = {r: RuleBasedAnalyst(r, profile=profile, policies=all_policies, facts=facts, today=today) for r in ("A", "B", "C")}
    pkg = build_package(profile, all_policies, facts, language=language, today=today)
    with ThreadPoolExecutor(max_workers=2) as ex:  # A and B in parallel; neither sees the other
        fa = ex.submit(_with_fallback, "A", lambda: providers["A"].analyze(pkg), lambda: rules["A"].analyze(pkg), meta)
        fb = ex.submit(_with_fallback, "B", lambda: providers["B"].analyze(pkg), lambda: rules["B"].analyze(pkg), meta)
        a_out: AnalystOutput = fa.result()
        b_out: AnalystOutput = fb.result()
    meta["A"].update({"latency_ms": a_out.latency_ms, "findings": len(a_out.findings), "usage": a_out.usage})
    meta["B"].update({"latency_ms": b_out.latency_ms, "findings": len(b_out.findings), "usage": b_out.usage})
    stages.done("analysts", A=meta["A"], B=meta["B"])

    # 5 challenge
    stages.start("challenge")
    ch: ChallengeOutput = _with_fallback("B", lambda: providers["B"].challenge(pkg, a_out, b_out), lambda: rules["B"].challenge(pkg, a_out, b_out), meta)
    stages.done("challenge", challenges=len(ch.challenges), additional_findings=len(ch.additional_findings), by_type={t: sum(1 for c in ch.challenges if c.type == t) for t in {c.type for c in ch.challenges}})

    # candidates after the challenge: A's findings plus B's additional ones
    candidates: list[CandidateFinding] = list(a_out.findings) + list(ch.additional_findings)
    for f in candidates:
        for c in ch.challenges:
            if c.target_key == f.key and c.type in {"missing_exclusion", "incorrect_assumption", "alternative_reading"}:
                f.assumptions.append(f"challenged: {c.note}")
    dicts = {f.key: _candidate_to_dict(f, clause_index) for f in candidates}
    declared = [float(a.get("value") or 0) for a in (profile.assets or [])]
    vctx = verification.build_context(user.id, all_policies, facts, declared)
    pre_verification = {k: verification.verify(d, vctx) for k, d in dicts.items()}

    # 6 judge
    stages.start("judge")
    judge: JudgeOutput = _with_fallback("C", lambda: providers["C"].adjudicate(pkg, a_out, b_out, ch, pre_verification), lambda: rules["C"].adjudicate(pkg, a_out, b_out, ch, pre_verification), meta)
    verdicts = {v.key: v for v in judge.verdicts}
    meta["C"].update({"latency_ms": judge.latency_ms, "usage": judge.usage})
    stages.done("judge", accepted=sum(1 for v in judge.verdicts if v.verdict == "accepted"), rejected=sum(1 for v in judge.verdicts if v.verdict == "rejected"), revise=sum(1 for v in judge.verdicts if v.verdict == "revise"), unresolved=sum(1 for v in judge.verdicts if v.verdict == "unresolved"), C=meta["C"])

    # 7 verification with the judge's verdicts, applying revisions
    stages.start("verification")
    final: list[dict] = []
    rejected: list[dict] = []
    for f in candidates:
        d = dicts[f.key]
        v = verdicts.get(f.key)
        if v and v.verdict == "rejected":
            rejected.append({"key": f.key, "need": f.need, "reason": v.reason, "proposed_by": f.proposed_by})
            continue
        if v and v.verdict == "revise":
            for cid in v.qualification_clause_ids:
                hit = clause_index.get(cid)
                if hit and all(e["clause_id"] != cid for e in d["evidence"]):
                    p, c = hit
                    d["evidence"].append({"clause_id": c.id, "policy_number": p.policy_number, "clause_ref": c.clause_ref, "citation": f"{p.policy_number} §{c.clause_ref}", "title": c.title, "kind": c.kind, "amount": c.amount, "note": "qualification"})
                    d["explanation"] = {"en": f"{d['explanation'].get('en', '')} Qualification: [{p.policy_number} §{c.clause_ref}] ({c.title}) may narrow or exclude this.", "zh": f"{d['explanation'].get('zh', '')} 限定：[{p.policy_number} §{c.clause_ref}]（{c.title}）可能缩小或排除该保障。"}
            if v.revised_statement:
                d["explanation"] = v.revised_statement
        if v and v.verdict == "unresolved":
            d["kind"] = "unresolved"
        d["models"]["judge"] = {"verdict": v.verdict if v else "pending", "reason": v.reason if v else "", "model": meta["C"]["model"]}
        d["models"]["challenges"] = [{"type": c.type, "note": c.note} for c in ch.challenges if c.target_key == f.key]
        d["verification"] = verification.verify(d, vctx, judge={"verdict": {"accepted": "accepted", "revise": "revise", "unresolved": "unresolved"}.get(v.verdict, "pending") if v else "pending", "reason": v.reason if v else ""})
        d["contradictions"] = d["verification"].get("contradictions", [])
        final.append(d)
    stages.done("verification", verified=sum(1 for d in final if d["verification"]["overall"] == "verified"), qualified=sum(1 for d in final if d["verification"]["overall"] == "qualified"), requires_review=sum(1 for d in final if d["verification"]["overall"] == "requires_review"), rejected=len(rejected))

    # 8 calculation
    stages.start("calculation")
    n_calc = 0
    for d in final:
        calc = _illustrate(d, profile, by_number, facts, clause_index)
        d["calculation"] = calc
        if calc and calc.get("status") == "illustrative":
            n_calc += 1
            d["verification"] = verification.verify(d, vctx, judge=d["verification"].get("independent_check") and {"verdict": d["verification"]["independent_check"], "reason": d["verification"].get("independent_reason", "")}, calculation=calc)
    stages.done("calculation", illustrative=n_calc)

    # 9 report: prioritise, persist findings, graph and analysis
    stages.start("report")
    end_dates = {p.policy_number: p.end_date for p, _ in all_policies}
    waiting_active = any(d["need"] == "waiting_period_active" for d in final)
    for d in final:
        # evidence entries carry the clause text so the interface can open the clause without another request
        for e in d.get("evidence", []):
            hit = clause_index.get(e.get("clause_id", ""))
            if hit:
                _, c = hit
                e.update({"text": " ".join((c.text or "").split())[:900], "section": c.section, "language": c.language, "translations": c.translations or {}, "tags": c.tags or [], "kind": c.kind, "title": c.title})
        pr = prioritisation.prioritise(d, verification=d["verification"], concerns=profile.concerns or [], policy_end_dates=end_dates, today=today, waiting_period_active=waiting_active and d["need"] in {"pet_vet_fees", "ebike_theft"})
        d.update({"risk_priority": pr["risk_priority"], "evidence_confidence": pr["evidence_confidence"], "priority_rationale": pr["rationale"], "priority_score": pr["score"]})
        d["requires_human_review"] = d["verification"]["overall"] == "requires_review" or d["kind"] == "unresolved"
        d["review_status"] = "none"
        d["statement"] = d.get("explanation")
    order = {"critical_to_investigate": 0, "high": 1, "medium": 2, "information_needed": 3, "low": 4}
    status_order = {"gap": 0, "partial": 1, "duplicate": 2, "note": 3, "covered": 4}
    final.sort(key=lambda d: (order.get(d["risk_priority"], 9), status_order.get(d["status"], 9), {"high": 0, "medium": 1, "low": 2}.get(d["severity"], 9)))
    counts = {k: sum(1 for d in final if d["status"] == k) for k in ("gap", "partial", "duplicate", "covered", "note")}
    counts["unresolved"] = sum(1 for d in final if d["kind"] == "unresolved")
    counts["requires_review"] = sum(1 for d in final if d["requires_human_review"])
    counts["verified"] = sum(1 for d in final if d["verification"]["overall"] == "verified")
    mode_label = "deterministic" if all(m.get("provider") == "rules" for m in meta.values()) else "models" if all(m.get("provider") == "openai_compatible" and not m.get("fallback") for m in meta.values()) else "mixed"
    summary = {"counts": counts, "policies_considered": [p.policy_number for p, _ in active], "policies_excluded": [{"policy_number": p.policy_number, "status": p.status} for p, _ in all_policies if p.status != "active"], "assets_considered": len(profile.assets or []), "activities_considered": len(profile.activities or []), "facts_extracted": n_facts, "rejected": rejected, "missing_evidence": judge.missing_evidence, "next_steps": judge.next_steps, "mode": mode_label, "pipeline_version": PIPELINE_VERSION, "by_priority": {k: sum(1 for d in final if d["risk_priority"] == k) for k in order}}
    ga = GapAnalysis(user_id=user.id, run_id=run_id, findings=[], summary=summary, inputs_snapshot={"assets": profile.assets, "activities": profile.activities, "housing": profile.housing, "household": profile.household, "policies": [p.policy_number for p, _ in all_policies], "today": today.isoformat()}, job_id=job.id if job else None, mode=mode_label, models=meta, pipeline_version=PIPELINE_VERSION)
    db.add(ga)
    db.flush()
    for d in final:
        row = CoverageFinding(analysis_id=ga.id, user_id=user.id, need=d["need"], kind=d["kind"], status=d["status"], severity=d["severity"], risk_priority=d["risk_priority"], evidence_confidence=d["evidence_confidence"], payload={}, verification=d["verification"], requires_human_review=d["requires_human_review"])
        db.add(row)
        db.flush()
        d["id"] = row.id
        row.payload = d
    ga.findings = final
    g = graph_svc.build(user, profile, all_policies, facts, final, claims)
    graph_svc.persist(db, ga.id, user.id, g)
    if job is not None:
        job.analysis_id, job.status, job.models = ga.id, "completed", meta
        db.commit()
    else:
        db.flush()  # inside an agent run the caller owns the transaction
    audit.record(db, actor_type="agent" if run_id else "customer", actor_id=user.id, action="gap_analysis.run", target_type="gap_analysis", target_id=ga.id, details={"counts": counts, "mode": mode_label, "job_id": job.id if job else None, "run_id": run_id}, commit=job is not None)
    stages.done("report", findings=len(final), nodes=len(g.nodes), edges=len(g.edges), mode=mode_label)
    return ga


def analysis_dict(db: Session, ga: GapAnalysis, *, include_graph: bool = True) -> dict:
    out = {"id": ga.id, "created_at": ga.created_at.isoformat(), "findings": ga.findings, "summary": ga.summary, "inputs_snapshot": ga.inputs_snapshot, "disclaimer": DISCLAIMER, "mode": ga.mode or "deterministic", "models": ga.models or {}, "pipeline_version": ga.pipeline_version, "job_id": ga.job_id}
    if include_graph:
        out["graph"] = graph_svc.load(db, ga.id)
    return out


def job_dict(job: CoverageAnalysisJob) -> dict:
    return {"id": job.id, "status": job.status, "mode": job.mode, "stages": job.stages, "models": job.models, "analysis_id": job.analysis_id, "error": job.error, "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat() if job.updated_at else None}


def _run_job(job_id: str, user_id: str, mode: str | None, language: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(CoverageAnalysisJob, job_id)
        user = db.get(User, user_id)
        if not job or not user:
            return
        try:
            run(db, user, mode=mode, language=language, job=job)
        except Exception as exc:  # recorded on the job, never swallowed silently
            log.error("analysis job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
            db.rollback()
            job = db.get(CoverageAnalysisJob, job_id)
            if job:
                job.status, job.error = "failed", str(exc)[:500]
                for s in job.stages or []:
                    if s.get("status") == "running":
                        s["status"] = "failed"
                job.stages = [dict(s) for s in (job.stages or [])]
                db.commit()
    finally:
        db.close()


def start(db: Session, user: User, *, mode: str | None = None, language: str = "en", background: bool | None = None) -> CoverageAnalysisJob:
    job = CoverageAnalysisJob(user_id=user.id, mode=mode or settings.effective_analysis_mode, status="queued", stages=[{"key": k, "status": "pending", "started_at": None, "ended_at": None, "detail": {}} for k in STAGE_KEYS])
    db.add(job)
    db.commit()
    background = settings.analysis_run_in_background if background is None else background
    if background:
        threading.Thread(target=_run_job, args=(job.id, user.id, mode, language), daemon=True, name=f"analysis-{job.id[:8]}").start()
    else:
        try:
            run(db, user, mode=mode, language=language, job=job)
        except Exception as exc:
            db.rollback()
            job = db.get(CoverageAnalysisJob, job.id) or job
            job.status, job.error = "failed", str(exc)[:500]
            db.commit()
    return job
