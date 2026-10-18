"""Portfolio views: policy cards, renewal reminders, dashboard overview and
the action centre. All figures are computed from stored records."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import ApprovalRequest, Claim, ClaimStatus, Communication, CustomerProfile, Document, GapAnalysis, InformationRequest, Notification, Policy, PolicyClause, User, utcnow
from .taxonomy import insurance_label

DEFAULT_REMINDER_DAYS = [60, 30, 14, 7]


def user_policies(db: Session, user_id: str, *, include_expired: bool = True) -> list[Policy]:
    q = select(Policy).where(Policy.user_id == user_id)
    if not include_expired:
        q = q.where(Policy.status == "active")
    return list(db.execute(q.order_by(Policy.end_date.desc())).scalars())


def policies_with_clauses(db: Session, user_id: str, *, only_active: bool = False) -> list[tuple[Policy, list[PolicyClause]]]:
    pols = user_policies(db, user_id, include_expired=not only_active)
    if not pols:
        return []
    clauses = db.execute(select(PolicyClause).options(selectinload(PolicyClause.policy)).where(PolicyClause.policy_id.in_([p.id for p in pols])).order_by(PolicyClause.position)).scalars().all()
    by_policy: dict[str, list[PolicyClause]] = {p.id: [] for p in pols}
    for c in clauses:
        by_policy[c.policy_id].append(c)
    return [(p, by_policy[p.id]) for p in pols]


def annualised(policy: Policy) -> float:
    if policy.premium_amount is None:
        return 0.0
    return float(policy.premium_amount) * (12 if policy.premium_frequency == "monthly" and policy.premium_amount < 200 else 1)


def policy_card(policy: Policy, lang: str = "en", *, claims_open: int = 0) -> dict:
    today = date.today()
    days_to_end = (policy.end_date - today).days
    renewal_status = "expired" if policy.status == "expired" or days_to_end < 0 else ("renewing_soon" if days_to_end <= 60 else "active")
    return {
        "id": policy.id,
        "provider_name": policy.provider_name,
        "product_name": policy.product_name,
        "insurance_type": policy.insurance_type,
        "insurance_type_label": insurance_label(policy.insurance_type, lang),
        "policy_number": policy.policy_number,
        "language": policy.language,
        "country": policy.country,
        "currency": policy.currency,
        "start_date": policy.start_date.isoformat(),
        "end_date": policy.end_date.isoformat(),
        "days_to_end": days_to_end,
        "premium_amount": policy.premium_amount,
        "premium_frequency": policy.premium_frequency,
        "excess_amount": policy.excess_amount,
        "status": policy.status,
        "renewal_status": renewal_status,
        "source": policy.source,
        "insurer_org_id": policy.insurer_org_id,
        "document_id": policy.document_id,
        "summary": policy.summary or {},
        "extraction_confidence": policy.extraction_confidence,
        "is_demo_data": policy.is_demo_data,
        "open_claims": claims_open,
    }


def clause_dict(c: PolicyClause, policy_number: str | None = None) -> dict:
    return {
        "id": c.id, "policy_id": c.policy_id, "clause_ref": c.clause_ref, "citation": f"{policy_number or c.policy.policy_number} §{c.clause_ref}", "section": c.section, "title": c.title,
        "text": c.text, "language": c.language, "kind": c.kind, "tags": c.tags, "amount": c.amount, "page": c.page, "confidence": c.confidence, "translations": c.translations or {},
    }


def renewal_reminders(policies: list[Policy], reminder_days: list[int] | None = None, lang: str = "en") -> list[dict]:
    today = date.today()
    days = sorted(reminder_days or DEFAULT_REMINDER_DAYS, reverse=True)
    out = []
    for p in policies:
        if p.status != "active":
            continue
        left = (p.end_date - today).days
        if 0 <= left <= days[0]:
            stage = next((d for d in days if left <= d), days[0])
            out.append({"policy_id": p.id, "policy_number": p.policy_number, "product": f"{p.provider_name} {p.product_name}", "end_date": p.end_date.isoformat(), "days_left": left, "stage": stage, "message": (f"{p.provider_name} {p.product_name} renews in {left} days" if lang != "zh" else f"{p.provider_name}{p.product_name} 将于 {left} 天后到期")})
    out.sort(key=lambda r: r["days_left"])
    return out


def overview(db: Session, user: User) -> dict:
    lang = user.locale or "en"
    pols = user_policies(db, user.id)
    active = [p for p in pols if p.status == "active"]
    claims = list(db.execute(select(Claim).where(Claim.user_id == user.id).order_by(Claim.updated_at.desc())).scalars())
    open_claims = [c for c in claims if c.status not in ClaimStatus.TERMINAL and c.status not in ClaimStatus.DECIDED]
    profile = db.get(CustomerProfile, user.id)
    reminder_days = (profile.preferences or {}).get("reminders_days") if profile else None
    reminders = renewal_reminders(pols, reminder_days, lang)
    info_requests = list(db.execute(select(InformationRequest).join(Claim, Claim.id == InformationRequest.claim_id).where(Claim.user_id == user.id, InformationRequest.status == "open")).scalars())
    unread_inbound = db.execute(select(Communication).where(Communication.user_id == user.id, Communication.direction == "inbound", Communication.read_at.is_(None))).scalars().all()
    pending_approvals = db.execute(select(ApprovalRequest).where(ApprovalRequest.user_id == user.id, ApprovalRequest.status == "pending", ApprovalRequest.expires_at > utcnow())).scalars().all()
    latest_gap = db.execute(select(GapAnalysis).where(GapAnalysis.user_id == user.id).order_by(GapAnalysis.created_at.desc()).limit(1)).scalar_one_or_none()
    by_currency: dict[str, float] = {}
    for p in active:
        by_currency[p.currency] = by_currency.get(p.currency, 0.0) + annualised(p)
    actions: list[dict] = []
    for c in open_claims:
        if c.status in {"draft", "information_required"}:
            missing = (c.completeness or {}).get("missing_required_documents", []) + (c.completeness or {}).get("missing_fields", [])
            actions.append({"type": "claim_incomplete", "priority": "medium", "title": (f"Finish claim {c.reference}" if lang != "zh" else f"完成理赔 {c.reference}"), "detail": ", ".join(missing[:4]), "link": f"/claims/{c.id}"})
        if c.status == "ready_for_review":
            actions.append({"type": "claim_ready", "priority": "medium", "title": (f"Review and submit {c.reference}" if lang != "zh" else f"确认并提交 {c.reference}"), "detail": "", "link": f"/claims/{c.id}"})
        if c.status == "submission_failed":
            actions.append({"type": "claim_failed", "priority": "high", "title": (f"Retry submission of {c.reference}" if lang != "zh" else f"重新提交 {c.reference}"), "detail": "", "link": f"/claims/{c.id}"})
    for r in info_requests:
        actions.append({"type": "information_requested", "priority": "high", "title": ("Insurer asked for more information" if lang != "zh" else "保险公司要求补充材料"), "detail": r.message[:120], "link": f"/claims/{r.claim_id}", "due_date": r.due_date.isoformat() if r.due_date else None})
    for a in pending_approvals:
        actions.append({"type": "approval_pending", "priority": "high", "title": ("An action is waiting for your approval" if lang != "zh" else "有操作等待你的批准"), "detail": a.summary, "link": "/assistant"})
    for r in reminders:
        actions.append({"type": "renewal", "priority": "low" if r["days_left"] > 14 else "medium", "title": r["message"], "detail": r["end_date"], "link": f"/policies/{r['policy_id']}"})
    if unread_inbound:
        actions.append({"type": "messages", "priority": "medium", "title": (f"{len(unread_inbound)} unread message(s) from insurers" if lang != "zh" else f"{len(unread_inbound)} 条未读保险公司消息"), "detail": "", "link": "/messages"})
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda a: priority_rank.get(a["priority"], 3))
    insights = []
    if latest_gap:
        for f in latest_gap.findings:
            if f["status"] in {"gap", "partial", "duplicate"}:
                insights.append({"status": f["status"], "severity": f["severity"], "title": f["label"].get(lang, f["label"]["en"]), "detail": f["explanation"].get(lang, f["explanation"]["en"]), "link": "/gaps"})
    claims_by_policy: dict[str, int] = {}
    for c in open_claims:
        if c.policy_id:
            claims_by_policy[c.policy_id] = claims_by_policy.get(c.policy_id, 0) + 1
    return {
        "metrics": {
            "active_policies": len(active),
            "annual_premium_by_currency": {k: round(v, 2) for k, v in by_currency.items()},
            "monthly_premium_by_currency": {k: round(v / 12, 2) for k, v in by_currency.items()},
            "renewals_next_60_days": len(reminders),
            "open_claims": len(open_claims),
            "outstanding_actions": len(actions),
            "potential_gaps": sum(1 for i in insights if i["status"] == "gap"),
            "gap_analysis_at": latest_gap.created_at.isoformat() if latest_gap else None,
        },
        "policies": [policy_card(p, lang, claims_open=claims_by_policy.get(p.id, 0)) for p in pols],
        "actions": actions[:12],
        "insights": insights[:6],
        "recent_claims": [claim_summary(c, lang) for c in claims[:5]],
        "data_note": {"en": "Demonstration environment: policies, claims and insurers are fictional.", "zh": "演示环境：保单、理赔和保险公司均为虚构。"},
    }


def claim_summary(c: Claim, lang: str = "en") -> dict:
    from .claims import STATUS_LABELS

    return {
        "id": c.id, "reference": c.reference, "policy_id": c.policy_id, "insurance_type": c.insurance_type, "incident_type": c.incident_type,
        "incident_date": c.incident_date.isoformat() if c.incident_date else None, "status": c.status, "status_label": STATUS_LABELS.get(c.status, {}).get(lang, c.status),
        "status_source": c.status_source, "status_updated_at": c.status_updated_at.isoformat(), "estimated_loss": c.estimated_loss, "currency": c.currency,
        "progress": (c.completeness or {}).get("progress"), "insurer_reference": c.insurer_reference, "updated_at": c.updated_at.isoformat(), "created_at": c.created_at.isoformat(),
        "priority": c.priority, "assigned_handler_id": c.assigned_handler_id, "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
    }


def timeline_for_policy(db: Session, policy: Policy) -> list[dict]:
    items = [{"at": policy.start_date.isoformat(), "type": "inception", "summary": f"Policy {policy.policy_number} started"}]
    claims = db.execute(select(Claim).where(Claim.policy_id == policy.id).order_by(Claim.created_at)).scalars().all()
    for c in claims:
        items.append({"at": c.created_at.date().isoformat(), "type": "claim", "summary": f"Claim {c.reference} ({c.incident_type.replace('_', ' ')}) — {c.status.replace('_', ' ')}", "link": f"/claims/{c.id}"})
    comms = db.execute(select(Communication).where(Communication.policy_id == policy.id).order_by(Communication.created_at)).scalars().all()
    for m in comms:
        items.append({"at": m.created_at.date().isoformat(), "type": "communication", "summary": f"{'Sent' if m.direction == 'outbound' else 'Received'}: {m.subject}"})
    items.append({"at": policy.end_date.isoformat(), "type": "renewal", "summary": f"Policy ends / renewal due"})
    items.sort(key=lambda i: i["at"])
    return items
