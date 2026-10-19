"""Typed, permission tiered tools the assistant can call.

Tiers
-----
0  read: look things up for the authenticated principal only
1  prepare: create or refresh drafts, checklists, analyses (no external effect)
2  act: anything that leaves the platform or commits the customer. The tool
   never performs the action; it creates an ApprovalRequest and returns
   ``approval_required``. The backend executes it only after the customer
   approves from the interface with a one time token.
3  human only: claim decisions, information requests to customers, assignment.
   No tool exists for these; insurer staff use dedicated endpoints.

Every tool: Pydantic input schema, explicit tier, role allow list, handler
that only ever receives the caller's own principal from the server (the
model cannot pass a user id), structured output, and an execution record
with masked inputs.
"""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..models import AgentRun, Claim, ClaimDecision, ClaimStatus, Communication, Conversation, CustomerProfile, Document, GapAnalysis, InformationRequest, Notification, Organization, Policy, PolicyClause, Roles, ToolExecution, User, utcnow
from ..pii import mask_value
from ..services import approvals as approval_svc
from ..services import claims as claim_svc
from ..services import communications as comm_svc
from ..services import coverage as coverage_svc
from ..services import gap_engine
from ..services import policies as pol_svc
from ..services.extraction import sanitise_for_model
from ..services.retrieval import detect_language, index_from_rows, is_silent
from ..services.taxonomy import INCIDENT_TYPES, incident_label, questions_for

MAX_TIER_FOR_MODEL = 2


@dataclass
class ToolContext:
    db: Session
    user: User
    run: AgentRun
    conversation: Conversation | None
    language: str = "en"
    surface: str = "customer"  # customer | insurer_copilot
    retrieved_citations: set[str] = field(default_factory=set)
    retrieved_clauses: dict[str, dict] = field(default_factory=dict)
    created_approvals: list[dict] = field(default_factory=list)
    touched_claim_ids: list[str] = field(default_factory=list)
    blocks: list[dict] = field(default_factory=list)
    step: int = 0
    oplog: Any = None

    def remember_clauses(self, items: list[dict]) -> None:
        for c in items:
            cit = c.get("citation")
            if cit:
                self.retrieved_citations.add(cit)
                self.retrieved_clauses[cit] = c


@dataclass
class ToolSpec:
    name: str
    description: str
    model: type[BaseModel]
    tier: int
    roles: set[str]
    handler: Callable[[ToolContext, BaseModel], dict]
    surfaces: set[str] = field(default_factory=lambda: {"customer"})

    def json_schema(self) -> dict:
        schema = self.model.model_json_schema()
        schema.pop("title", None)
        return _inline_refs(schema)


def _inline_refs(schema: dict) -> dict:
    """Resolve local $ref/$defs so every tool schema is a self contained JSON Schema."""
    defs = schema.pop("$defs", {}) or {}

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node and node["$ref"].startswith("#/$defs/"):
                target = defs.get(node["$ref"].split("/")[-1], {})
                merged = {k: v for k, v in target.items() if k != "title"}
                merged.update({k: v for k, v in node.items() if k != "$ref"})
                return walk(merged)
            return {k: walk(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


class ToolError(Exception):
    def __init__(self, message: str, *, code: str = "tool_error", safe_next_step: str | None = None):
        super().__init__(message)
        self.code = code
        self.safe_next_step = safe_next_step


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, description: str, *, tier: int, roles: set[str] | None = None, surfaces: set[str] | None = None):
    def deco(fn: Callable[[ToolContext, BaseModel], dict]):
        model = inspect.get_annotations(fn, eval_str=True).get("params")
        if model is None:
            raise RuntimeError(f"tool {name} needs a 'params' annotation")
        REGISTRY[name] = ToolSpec(name=name, description=description, model=model, tier=tier, roles=roles or {Roles.CUSTOMER}, handler=fn, surfaces=surfaces or {"customer"})
        return fn

    return deco


def specs_for(user: User, surface: str) -> list[ToolSpec]:
    return [s for s in REGISTRY.values() if user.role in s.roles and surface in s.surfaces and s.tier <= MAX_TIER_FOR_MODEL]


# ----------------------------------------------------------------- helpers ---


def _policy_or_error(ctx: ToolContext, policy_id: str) -> Policy:
    p = ctx.db.get(Policy, policy_id)
    if not p or p.user_id != ctx.user.id:
        raise ToolError("policy not found in this customer's portfolio", code="not_found", safe_next_step="call get_insurance_portfolio to see valid policy ids")
    return p


def _claim_or_error(ctx: ToolContext, claim_id: str) -> Claim:
    c = ctx.db.get(Claim, claim_id)
    if not c or c.user_id != ctx.user.id:
        raise ToolError("claim not found for this customer", code="not_found", safe_next_step="call list_claims to see valid claim ids")
    if c.id not in ctx.touched_claim_ids:
        ctx.touched_claim_ids.append(c.id)
    return c


def _clauses_for(ctx: ToolContext, policy_ids: list[str]) -> list[PolicyClause]:
    if not policy_ids:
        return []
    return list(ctx.db.execute(select(PolicyClause).options(selectinload(PolicyClause.policy)).where(PolicyClause.policy_id.in_(policy_ids)).order_by(PolicyClause.position)).scalars())


def _org(ctx: ToolContext, org_id: str | None) -> Organization | None:
    return ctx.db.get(Organization, org_id) if org_id else None


def _claim_payload(ctx: ToolContext, c: Claim) -> dict:
    lang = ctx.language
    docs = claim_svc.claim_documents(ctx.db, c)
    policy = ctx.db.get(Policy, c.policy_id) if c.policy_id else None
    open_requests = [
        {"id": r.id, "message": r.message, "items": r.requested_items, "due_date": r.due_date.isoformat() if r.due_date else None, "created_at": r.created_at.isoformat()}
        for r in ctx.db.execute(select(InformationRequest).where(InformationRequest.claim_id == c.id, InformationRequest.status == "open")).scalars()
    ]
    decision = ctx.db.execute(select(ClaimDecision).where(ClaimDecision.claim_id == c.id).order_by(ClaimDecision.created_at.desc()).limit(1)).scalar_one_or_none()
    return {
        **pol_svc.claim_summary(c, lang),
        "policy_number": policy.policy_number if policy else None,
        "policy_name": f"{policy.provider_name} {policy.product_name}" if policy else None,
        "insurer": (_org(ctx, c.insurer_org_id).name if c.insurer_org_id else None),
        "incident": {"type": c.incident_type, "type_label": incident_label(c.incident_type, lang), "date": c.incident_date.isoformat() if c.incident_date else None, "time": c.incident_time, "location": c.location, "description": c.description, "items_involved": c.items_involved, "estimated_loss": c.estimated_loss, "loss_unknown": c.loss_unknown, "third_party_involved": c.third_party_involved, "authorities_involved": c.authorities_involved, "authority_reference": c.authority_reference, "actions_taken": c.actions_taken, "safety_concern": c.safety_concern},
        "checklist": c.checklist,
        "completeness": c.completeness,
        "coverage_view": c.coverage_view,
        "documents": [{"id": d.id, "filename": d.filename, "doc_type": d.doc_type, "checklist_item_key": d.checklist_item_key, "uploaded_at": d.created_at.isoformat(), "submitted": d.submitted_at is not None, "injection_flags": len(d.injection_flags or [])} for d in docs],
        "open_information_requests": open_requests,
        "decision": ({"outcome": decision.outcome, "reason": decision.reason, "clause_refs": decision.clause_refs, "approved_amount": decision.approved_amount, "currency": decision.currency, "decided_at": decision.created_at.isoformat()} if decision else None),
        "status_note": {"en": f"Last known status '{c.status}' from {c.status_source} at {c.status_updated_at.isoformat()}", "zh": f"最近已知状态“{c.status}”，来源 {c.status_source}，时间 {c.status_updated_at.isoformat()}"},
    }


# ================================================================ TIER 0 ===


class NoParams(BaseModel):
    pass


@tool("get_customer_profile", "Return the customer's own profile: country, housing, household, declared assets and activities. Use it to understand their circumstances before analysing cover.", tier=0)
def get_customer_profile(ctx: ToolContext, params: NoParams) -> dict:
    prof = ctx.db.get(CustomerProfile, ctx.user.id)
    if not prof:
        return {"profile": None, "note": "no profile yet"}
    return {"profile": {"name": ctx.user.full_name, "country": prof.country, "city": prof.city, "currency": prof.currency, "housing": prof.housing, "household": prof.household, "assets": prof.assets, "activities": prof.activities, "concerns": prof.concerns, "language": ctx.user.locale}}


class PortfolioParams(BaseModel):
    include_expired: bool = Field(False, description="Include expired or cancelled policies")


@tool("get_insurance_portfolio", "List the customer's policies with ids, policy numbers, type, provider, dates, premium, excess and key limits. Always call this before answering questions about 'my policy' so you use real policy ids.", tier=0)
def get_insurance_portfolio(ctx: ToolContext, params: PortfolioParams) -> dict:
    pols = pol_svc.user_policies(ctx.db, ctx.user.id, include_expired=params.include_expired)
    return {"policies": [pol_svc.policy_card(p, ctx.language) for p in pols], "count": len(pols)}


class PolicyParams(BaseModel):
    policy_id: str = Field(..., description="Policy id from get_insurance_portfolio")
    include_clauses: bool = Field(True, description="Include the full clause list")


@tool("get_policy_details", "Return one policy with its schedule summary and every clause (reference, title, kind, text, tags). Use when the customer asks about a specific policy or you need the full wording.", tier=0)
def get_policy_details(ctx: ToolContext, params: PolicyParams) -> dict:
    p = _policy_or_error(ctx, params.policy_id)
    card = pol_svc.policy_card(p, ctx.language)
    clauses = _clauses_for(ctx, [p.id])
    out = {"policy": card, "clause_count": len(clauses)}
    if params.include_clauses:
        items = [pol_svc.clause_dict(c, p.policy_number) for c in clauses]
        out["clauses"] = items
        ctx.remember_clauses(items)
    return out


class SearchParams(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="The customer's question or the facts to look up, in any language")
    policy_ids: list[str] | None = Field(None, description="Restrict to these policies; default all of the customer's policies")
    kinds: list[str] | None = Field(None, description="Restrict to clause kinds: coverage, exclusion, condition, limit, definition, claims_procedure")
    top_k: int = Field(8, ge=1, le=20)


@tool("search_policy_wording", "Search the customer's policy wordings and return the most relevant clauses with exact citations in the form 'POLICY-NUMBER §ref'. Cite only clauses returned here. If 'silent' is true, the wording does not address the question: say so instead of guessing.", tier=0)
def search_policy_wording(ctx: ToolContext, params: SearchParams) -> dict:
    pols = pol_svc.user_policies(ctx.db, ctx.user.id)
    allowed = {p.id for p in pols}
    ids = [i for i in (params.policy_ids or []) if i in allowed] or list(allowed)
    if params.policy_ids and not ids:
        raise ToolError("none of the policy ids belong to this customer", code="not_found")
    clauses = _clauses_for(ctx, ids)
    index = index_from_rows(clauses)
    hits = index.search(params.query, kinds=set(params.kinds) if params.kinds else None, top_k=params.top_k)
    silent = is_silent(params.query, hits)
    items = [h.to_dict() for h in hits]
    for it in items:
        it["text"] = sanitise_for_model(it["text"], max_chars=1800)
    ctx.remember_clauses(items)
    return {"query_language": detect_language(params.query), "silent": silent, "hits": items, "policies_searched": [p.policy_number for p in pols if p.id in ids], "citation_format": "[POLICY-NUMBER §ref]"}


class ListClaimsParams(BaseModel):
    status: str | None = Field(None, description="Optional status filter")


@tool("list_claims", "List the customer's claims with status, dates and progress.", tier=0)
def list_claims(ctx: ToolContext, params: ListClaimsParams) -> dict:
    q = select(Claim).where(Claim.user_id == ctx.user.id)
    if params.status:
        q = q.where(Claim.status == params.status)
    claims = ctx.db.execute(q.order_by(Claim.updated_at.desc())).scalars().all()
    return {"claims": [pol_svc.claim_summary(c, ctx.language) | {"incident_description": c.description[:160]} for c in claims]}


class ClaimParams(BaseModel):
    claim_id: str = Field(..., description="Claim id from list_claims or create_claim_draft")


@tool("get_claim_details", "Return a claim with incident facts, checklist, completeness, coverage reading, documents, open insurer requests and any decision.", tier=0)
def get_claim_details(ctx: ToolContext, params: ClaimParams) -> dict:
    c = _claim_or_error(ctx, params.claim_id)
    payload = _claim_payload(ctx, c)
    for group in ("covering_clauses", "possible_exclusions", "conditions", "financial_terms"):
        ctx.remember_clauses(payload["coverage_view"].get(group, []) or [])
    for item in c.checklist:
        ctx.remember_clauses(item.get("citations", []))
    return {"claim": payload}


@tool("get_claim_timeline", "Return the chronological activity history of a claim.", tier=0)
def get_claim_timeline(ctx: ToolContext, params: ClaimParams) -> dict:
    c = _claim_or_error(ctx, params.claim_id)
    return {"claim_reference": c.reference, "timeline": claim_svc.timeline(ctx.db, c, for_customer=True)}


@tool("get_claim_decision", "Return the insurer's recorded decision on a claim together with the policy clauses the insurer relied on, so you can explain it and identify what is missing.", tier=0)
def get_claim_decision(ctx: ToolContext, params: ClaimParams) -> dict:
    c = _claim_or_error(ctx, params.claim_id)
    decision = ctx.db.execute(select(ClaimDecision).where(ClaimDecision.claim_id == c.id).order_by(ClaimDecision.created_at.desc()).limit(1)).scalar_one_or_none()
    if not decision:
        return {"decision": None, "note": "no decision has been recorded on this claim"}
    clauses = _clauses_for(ctx, [c.policy_id]) if c.policy_id else []
    relied = [pol_svc.clause_dict(cl) for cl in clauses if cl.clause_ref in (decision.clause_refs or [])]
    routes = [pol_svc.clause_dict(cl) for cl in clauses if "complaints" in (cl.tags or []) or "reconsideration" in (cl.tags or [])]
    ctx.remember_clauses(relied + routes)
    staff = ctx.db.get(User, decision.decided_by_user_id)
    inbound = ctx.db.execute(select(Communication).where(Communication.claim_id == c.id, Communication.kind == "decision").order_by(Communication.created_at.desc()).limit(1)).scalar_one_or_none()
    return {
        "claim_reference": c.reference,
        "decision": {"outcome": decision.outcome, "reason": decision.reason, "clause_refs": decision.clause_refs, "approved_amount": decision.approved_amount, "currency": decision.currency, "decided_at": decision.created_at.isoformat(), "decided_by": staff.full_name if staff else None, "message": inbound.body if inbound else None},
        "clauses_relied_on": relied,
        "review_route_clauses": routes,
    }


# ================================================================ TIER 1 ===


@tool("analyze_coverage_gaps", "Run the coverage gap analysis across the customer's profile and all active policies. Returns findings (gap, partial, duplicate, covered, note) each with clause evidence. Stores the analysis for the dashboard.", tier=1)
def analyze_coverage_gaps(ctx: ToolContext, params: NoParams) -> dict:
    prof = ctx.db.get(CustomerProfile, ctx.user.id)
    if not prof:
        raise ToolError("the customer has no profile yet; ask them to complete it in Settings", code="missing_profile")
    # The same pipeline as the Coverage gaps page (analysts, challenge, judge,
    # verification, calculation), run synchronously in deterministic mode so
    # the chat answer and the page never disagree.
    from ..services import analysis as analysis_svc

    ga = analysis_svc.run(ctx.db, ctx.user, mode="deterministic", run_id=ctx.run.id, language=ctx.language)
    result = {"findings": ga.findings, "summary": ga.summary, "disclaimer": analysis_svc.DISCLAIMER}
    for f in result["findings"]:
        ctx.remember_clauses(f.get("evidence", []))
    shown = [f for f in result["findings"] if f["status"] != "covered"][:8] or result["findings"][:8]
    ctx.blocks.append({"type": "gap_findings", "analysis_id": ga.id, "findings": shown, "summary": result["summary"]})
    return {"analysis_id": ga.id, **result}


class AssessParams(BaseModel):
    incident_type: str = Field(..., description=f"One of: {', '.join(INCIDENT_TYPES)}")
    description: str = Field(..., min_length=3, max_length=4000, description="What happened, in the customer's words")
    policy_id: str | None = Field(None, description="If omitted, every policy is ranked for relevance")
    estimated_loss: float | None = None


@tool("assess_incident_coverage", "Read the policy wording against a described incident. Returns which clauses appear to cover it, possible exclusions, conditions and the excess, with citations and a conservative summary. Never a decision. Without policy_id it ranks all of the customer's policies.", tier=1)
def assess_incident_coverage(ctx: ToolContext, params: AssessParams) -> dict:
    if params.incident_type not in INCIDENT_TYPES:
        raise ToolError(f"unknown incident_type; use one of {', '.join(INCIDENT_TYPES)}", code="validation")
    if params.policy_id:
        p = _policy_or_error(ctx, params.policy_id)
        view = coverage_svc.coverage_view(p, _clauses_for(ctx, [p.id]), incident_type=params.incident_type, description=params.description, estimated_loss=params.estimated_loss)
        for group in ("covering_clauses", "possible_exclusions", "conditions", "financial_terms"):
            ctx.remember_clauses(view.get(group, []))
        ctx.blocks.append({"type": "coverage_view", "view": view})
        return {"coverage_view": view, "questions": questions_for(p.insurance_type, params.incident_type)}
    ranked = coverage_svc.rank_policies_for_incident(pol_svc.policies_with_clauses(ctx.db, ctx.user.id, only_active=True), incident_type=params.incident_type, description=params.description)
    for r in ranked:
        ctx.remember_clauses(r.get("covering_clauses", []) + r.get("possible_exclusions", []))
    return {"ranked_policies": ranked, "note": "pick the best policy_id and call assess_incident_coverage again or create_claim_draft"}


class ItemIn(BaseModel):
    label: str
    value: float | None = None


class CreateClaimParams(BaseModel):
    incident_type: str
    description: str = Field(..., min_length=3, max_length=4000)
    policy_id: str | None = None
    incident_date: date | None = None
    incident_time: str | None = None
    location: str | None = None
    items_involved: list[ItemIn] = Field(default_factory=list)
    estimated_loss: float | None = Field(None, description="Leave empty if the customer does not know")
    third_party_involved: bool | None = None
    authorities_involved: bool | None = None
    authority_reference: str | None = None
    actions_taken: str | None = None
    safety_concern: bool = False


@tool("create_claim_draft", "Create a claim draft from the incident facts the customer gave. Only record facts the customer stated; leave unknown fields empty. Returns the claim id, generated checklist, completeness and coverage reading.", tier=1)
def create_claim_draft(ctx: ToolContext, params: CreateClaimParams) -> dict:
    if params.incident_type not in INCIDENT_TYPES:
        raise ToolError(f"unknown incident_type; use one of {', '.join(INCIDENT_TYPES)}", code="validation")
    data = params.model_dump()
    data["items_involved"] = [i.model_dump() for i in params.items_involved]
    data["language"] = ctx.language
    try:
        claim = claim_svc.create_draft(ctx.db, ctx.user, data, actor_type="agent", run_id=ctx.run.id)
    except PermissionError as exc:
        raise ToolError(str(exc), code="forbidden")
    ctx.touched_claim_ids.append(claim.id)
    payload = _claim_payload(ctx, claim)
    for group in ("covering_clauses", "possible_exclusions", "conditions", "financial_terms"):
        ctx.remember_clauses(payload["coverage_view"].get(group, []) or [])
    ctx.blocks.append({"type": "claim_card", "claim": {k: payload[k] for k in ("id", "reference", "status", "status_label", "progress", "policy_number", "insurance_type", "incident_type")}})
    ctx.blocks.append({"type": "checklist", "claim_id": claim.id, "items": claim.checklist, "completeness": claim.completeness})
    return {"claim": payload, "next_step": "ask the customer to upload the required documents on the claim page, then check_claim_completeness"}


class UpdateClaimParams(BaseModel):
    claim_id: str
    fields: dict[str, Any] = Field(..., description="Fields to change: incident_date, incident_time, location, description, items_involved, estimated_loss, third_party_involved, authorities_involved, authority_reference, actions_taken, policy_id")


@tool("update_claim_draft", "Update facts on a claim that has not been submitted yet. Only change what the customer said.", tier=1)
def update_claim_draft(ctx: ToolContext, params: UpdateClaimParams) -> dict:
    claim = _claim_or_error(ctx, params.claim_id)
    try:
        claim_svc.update_draft(ctx.db, claim, params.fields, actor_type="agent", actor_id=None)
    except (claim_svc.TransitionError, PermissionError) as exc:
        raise ToolError(str(exc), code="invalid_state")
    return {"claim": _claim_payload(ctx, claim)}


@tool("generate_claim_checklist", "Regenerate the claim checklist from the policy's claims conditions and the incident. Each item says why it is needed and cites the clause.", tier=1)
def generate_claim_checklist(ctx: ToolContext, params: ClaimParams) -> dict:
    claim = _claim_or_error(ctx, params.claim_id)
    claim_svc.refresh_claim_analysis(ctx.db, claim)
    claim_svc.add_event(ctx.db, claim, actor_type="agent", actor_id=None, event_type="checklist_generated", summary="Checklist regenerated", data={"run_id": ctx.run.id})
    for item in claim.checklist:
        ctx.remember_clauses(item.get("citations", []))
    ctx.blocks.append({"type": "checklist", "claim_id": claim.id, "items": claim.checklist, "completeness": claim.completeness})
    return {"claim_reference": claim.reference, "checklist": claim.checklist, "completeness": claim.completeness}


@tool("check_claim_completeness", "Assess whether the claim pack has the fields and documents the insurer's procedure asks for. Administrative only: not a view on coverage.", tier=1)
def check_claim_completeness(ctx: ToolContext, params: ClaimParams) -> dict:
    claim = _claim_or_error(ctx, params.claim_id)
    claim_svc.refresh_claim_analysis(ctx.db, claim)
    ctx.blocks.append({"type": "completeness", "claim_id": claim.id, "completeness": claim.completeness, "checklist": claim.checklist})
    return {"claim_reference": claim.reference, "status": claim.status, "completeness": claim.completeness}


class CommDraftParams(BaseModel):
    kind: str = Field(..., description=f"One of: {', '.join(comm_svc.KINDS)}")
    claim_id: str | None = None
    policy_id: str | None = None
    points: list[str] = Field(default_factory=list, description="Specific points or questions the customer wants included, in their words")
    documents: list[str] = Field(default_factory=list, description="File names of documents to reference")
    subject_override: str | None = None
    body_override: str | None = Field(None, description="If you have written a better draft yourself from verified facts, pass it here; placeholders in [square brackets] for anything unknown")


@tool("create_communication_draft", "Draft a message or letter to the insurer from verified claim and policy facts. Nothing is sent: the draft is saved and the customer must approve sending it with send_communication. Never invent dates, reference numbers or promises; use [placeholders].", tier=1)
def create_communication_draft(ctx: ToolContext, params: CommDraftParams) -> dict:
    if params.kind not in comm_svc.KINDS:
        raise ToolError(f"unknown kind; use one of {', '.join(comm_svc.KINDS)}", code="validation")
    claim = _claim_or_error(ctx, params.claim_id) if params.claim_id else None
    policy = ctx.db.get(Policy, claim.policy_id) if (claim and claim.policy_id) else (_policy_or_error(ctx, params.policy_id) if params.policy_id else None)
    org = _org(ctx, (claim.insurer_org_id if claim else (policy.insurer_org_id if policy else None)))
    decision = ctx.db.execute(select(ClaimDecision).where(ClaimDecision.claim_id == claim.id).order_by(ClaimDecision.created_at.desc()).limit(1)).scalar_one_or_none() if claim else None
    d = comm_svc.draft(params.kind, user=ctx.user, claim=claim, policy=policy, org=org, decision=decision, points=params.points, documents=params.documents, lang=ctx.language)
    subject = params.subject_override or d["subject"]
    body = params.body_override or d["body"]
    comm = Communication(claim_id=claim.id if claim else None, policy_id=policy.id if policy else None, user_id=ctx.user.id, insurer_org_id=org.id if org else None, direction="outbound", channel="in_app", kind=params.kind, subject=subject, body=body, recipient=d["recipient"], status="draft", generated_by="agent", referenced_document_ids=[])
    ctx.db.add(comm)
    ctx.db.flush()
    if claim:
        claim_svc.add_event(ctx.db, claim, actor_type="agent", actor_id=None, event_type="draft_created", summary=f"Draft prepared: {subject}", data={"communication_id": comm.id, "run_id": ctx.run.id})
    ctx.blocks.append({"type": "communication_draft", "communication": {"id": comm.id, "kind": comm.kind, "subject": comm.subject, "body": comm.body, "recipient": comm.recipient, "status": comm.status, "claim_id": comm.claim_id}})
    audit.record(ctx.db, actor_type="agent", actor_id=ctx.user.id, action="communication.draft", target_type="communication", target_id=comm.id, details={"kind": params.kind, "run_id": ctx.run.id}, commit=False)
    return {"communication": {"id": comm.id, "kind": comm.kind, "subject": subject, "body": body, "recipient": comm.recipient, "status": "draft"}, "note": "draft only; sending requires send_communication and the customer's approval"}


class ReminderParams(BaseModel):
    title: str = Field(..., max_length=200)
    due_date: date
    link: str | None = None
    body: str | None = None


@tool("create_reminder", "Create a reminder for the customer (for example a renewal date or an insurer deadline that is stated in the wording). Only use dates that are verified facts.", tier=1)
def create_reminder(ctx: ToolContext, params: ReminderParams) -> dict:
    n = Notification(user_id=ctx.user.id, kind="reminder", title=params.title, body=(params.body or "") + f"\nDue: {params.due_date.isoformat()}", link=params.link, created_at=utcnow())
    ctx.db.add(n)
    ctx.db.flush()
    return {"reminder_id": n.id, "title": params.title, "due_date": params.due_date.isoformat()}


# ================================================================ TIER 2 ===


@tool("submit_claim", "Prepare submission of a claim to its insurer. Returns approval_required with a summary and consequences for the customer to approve in the interface. The claim is NOT submitted by this call.", tier=2)
def submit_claim(ctx: ToolContext, params: ClaimParams) -> dict:
    claim = _claim_or_error(ctx, params.claim_id)
    try:
        created = claim_svc.prepare_submission_approval(ctx.db, ctx.user, claim, run_id=ctx.run.id, actor_type="agent")
    except claim_svc.TransitionError as exc:
        raise ToolError(str(exc), code="not_ready", safe_next_step="call check_claim_completeness and help the customer finish the missing items")
    return _approval_result(ctx, created)


class SendCommParams(BaseModel):
    communication_id: str


@tool("send_communication", "Request approval to send a drafted message to the insurer. Nothing is sent until the customer approves.", tier=2)
def send_communication(ctx: ToolContext, params: SendCommParams) -> dict:
    comm = ctx.db.get(Communication, params.communication_id)
    if not comm or comm.user_id != ctx.user.id:
        raise ToolError("draft not found", code="not_found")
    try:
        created = approval_svc.create_communication_approval(ctx.db, ctx.user, comm, run_id=ctx.run.id, actor_type="agent")
    except (approval_svc.ApprovalError, PermissionError) as exc:
        raise ToolError(str(exc), code="invalid_state")
    return _approval_result(ctx, created)


class RespondParams(BaseModel):
    claim_id: str
    request_id: str
    response_text: str = Field(..., min_length=1, max_length=4000)
    document_ids: list[str] = Field(default_factory=list)


@tool("respond_to_information_request", "Request approval to send the customer's response (and selected uploaded documents) to an insurer's information request.", tier=2)
def respond_to_information_request(ctx: ToolContext, params: RespondParams) -> dict:
    claim = _claim_or_error(ctx, params.claim_id)
    req = ctx.db.get(InformationRequest, params.request_id)
    if not req or req.claim_id != claim.id:
        raise ToolError("information request not found on this claim", code="not_found")
    docs = ctx.db.execute(select(Document.id).where(Document.id.in_(params.document_ids), Document.claim_id == claim.id)).scalars().all() if params.document_ids else []
    try:
        created = approval_svc.create_information_response_approval(ctx.db, ctx.user, claim, req, params.response_text, list(docs), run_id=ctx.run.id, actor_type="agent")
    except (approval_svc.ApprovalError, PermissionError) as exc:
        raise ToolError(str(exc), code="invalid_state")
    return _approval_result(ctx, created)


def _approval_result(ctx: ToolContext, created: claim_svc.ApprovalCreated) -> dict:
    a = created.approval
    card = approval_svc.serialize(a, created.token)
    ctx.created_approvals.append(card)
    ctx.blocks.append({"type": "approval", "approval": card})
    return {"status": "approval_required", "approval_id": a.id, "summary": a.summary, "consequences": a.consequences, "expires_at": a.expires_at.isoformat(), "note": "Tell the customer to review and approve the card shown in the interface. Do not say the action has happened."}


# ====================================================== INSURER COPILOT ===


def _insurer_claim(ctx: ToolContext, claim_id: str) -> Claim:
    c = ctx.db.get(Claim, claim_id)
    if not c or c.insurer_org_id != ctx.user.org_id or c.status in ClaimStatus.PRE_SUBMISSION:
        raise ToolError("claim not found in this organisation's queue", code="not_found")
    if c.id not in ctx.touched_claim_ids:
        ctx.touched_claim_ids.append(c.id)
    return c


@tool("insurer_get_claim", "Return the claim file as the insurer sees it: facts as stated by the customer, documents with extraction status and injection flags, checklist, completeness, timeline, open requests and decisions. Facts are labelled by source.", tier=0, roles=Roles.INSURER_ROLES, surfaces={"insurer_copilot"})
def insurer_get_claim(ctx: ToolContext, params: ClaimParams) -> dict:
    c = _insurer_claim(ctx, params.claim_id)
    customer = ctx.db.get(User, c.user_id)
    policy = ctx.db.get(Policy, c.policy_id) if c.policy_id else None
    docs = claim_svc.claim_documents(ctx.db, c)
    decision = ctx.db.execute(select(ClaimDecision).where(ClaimDecision.claim_id == c.id).order_by(ClaimDecision.created_at.desc()).limit(1)).scalar_one_or_none()
    return {
        "claim": pol_svc.claim_summary(c, ctx.language),
        "customer_statement": {"source": "customer", "incident_type": c.incident_type, "incident_date": c.incident_date.isoformat() if c.incident_date else None, "location": c.location, "description": c.description, "items_involved": c.items_involved, "estimated_loss": c.estimated_loss, "authority_reference": c.authority_reference, "actions_taken": c.actions_taken},
        "customer_display": {"id": customer.id, "name": customer.full_name},
        "policy": pol_svc.policy_card(policy, ctx.language) if policy else None,
        "documents": [{"source": "customer_upload", "id": d.id, "filename": d.filename, "doc_type": d.doc_type, "extraction_status": d.extraction_status, "extracted_excerpt": sanitise_for_model(d.extracted_text or "", max_chars=600) or None, "injection_flags": d.injection_flags, "sha256": d.sha256[:12], "duplicate_of": None} for d in docs],
        "platform_checks": {"source": "platform_rules", "checklist": c.checklist, "completeness": c.completeness, "coverage_reading": {"verdict": c.coverage_view.get("verdict"), "covering_clauses": c.coverage_view.get("covering_clauses", []), "possible_exclusions": c.coverage_view.get("possible_exclusions", []), "note": "customer side reading, not an insurer determination"}},
        "timeline": claim_svc.timeline(ctx.db, c, for_customer=False),
        "open_requests": [{"id": r.id, "message": r.message, "items": r.requested_items, "status": r.status} for r in ctx.db.execute(select(InformationRequest).where(InformationRequest.claim_id == c.id)).scalars()],
        "decision": ({"outcome": decision.outcome, "reason": decision.reason, "clause_refs": decision.clause_refs, "approved_amount": decision.approved_amount} if decision else None),
    }


class InsurerSearchParams(BaseModel):
    claim_id: str
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(6, ge=1, le=15)


@tool("insurer_search_policy_wording", "Search the wording of the policy attached to a claim in this organisation's queue and return cited clauses.", tier=0, roles=Roles.INSURER_ROLES, surfaces={"insurer_copilot"})
def insurer_search_policy_wording(ctx: ToolContext, params: InsurerSearchParams) -> dict:
    c = _insurer_claim(ctx, params.claim_id)
    if not c.policy_id:
        return {"hits": [], "silent": True, "note": "no policy linked"}
    clauses = _clauses_for(ctx, [c.policy_id])
    hits = index_from_rows(clauses).search(params.query, top_k=params.top_k)
    items = [h.to_dict() for h in hits]
    ctx.remember_clauses(items)
    return {"silent": is_silent(params.query, hits), "hits": items}


class InsurerDraftParams(BaseModel):
    claim_id: str
    requested_items: list[str] = Field(..., min_length=1)
    clause_refs: list[str] = Field(default_factory=list, description="Clause references that justify the request")
    tone: str = Field("neutral", description="neutral | warm | formal")


@tool("insurer_draft_information_request", "Draft (do not send) a request for further information to the customer. The handler reviews and sends it from the case workspace.", tier=1, roles=Roles.INSURER_ROLES, surfaces={"insurer_copilot"})
def insurer_draft_information_request(ctx: ToolContext, params: InsurerDraftParams) -> dict:
    c = _insurer_claim(ctx, params.claim_id)
    customer = ctx.db.get(User, c.user_id)
    policy = ctx.db.get(Policy, c.policy_id) if c.policy_id else None
    refs = f" (see clause{'s' if len(params.clause_refs) > 1 else ''} {', '.join(params.clause_refs)} of your policy)" if params.clause_refs else ""
    items = "\n".join(f"- {i}" for i in params.requested_items)
    if ctx.language == "zh":
        body = f"{customer.full_name}您好：\n\n关于理赔 {c.reference}，为继续处理，请补充以下材料{refs}：\n{items}\n\n您可直接在理赔页面上传。谢谢。"
    else:
        body = f"Dear {customer.full_name},\n\nThank you for your claim {c.reference}. To continue our assessment we need the following{refs}:\n{items}\n\nYou can upload these directly to your claim. Thank you.\n\n{policy.provider_name if policy else 'Claims team'}"
    ctx.blocks.append({"type": "insurer_request_draft", "claim_id": c.id, "requested_items": params.requested_items, "message": body, "clause_refs": params.clause_refs})
    return {"draft": {"claim_id": c.id, "requested_items": params.requested_items, "message": body, "clause_refs": params.clause_refs}, "note": "not sent; the handler sends it from the case workspace"}


# ============================================================== EXECUTION ===


def execute(ctx: ToolContext, name: str, arguments: dict) -> dict:
    """Validate, authorise, run and record one tool call."""
    started = time.monotonic()
    ctx.step += 1
    spec = REGISTRY.get(name)
    record = ToolExecution(run_id=ctx.run.id, step=ctx.step, tool_name=name, tier=spec.tier if spec else -1, input_masked=mask_value(arguments), status="error")
    ctx.db.add(record)

    def finish(status: str, output: dict, error: str | None = None, approval_id: str | None = None) -> dict:
        record.status = status
        record.error = error
        record.approval_id = approval_id
        record.output_summary = _summarise_output(output)
        record.duration_ms = int((time.monotonic() - started) * 1000)
        ctx.db.flush()
        if ctx.oplog:
            ctx.oplog.tool(name=name, tier=record.tier, status=status, step=ctx.step, input_masked=record.input_masked, output_summary=record.output_summary, duration_ms=record.duration_ms, error=error, approval_id=approval_id)
        return output

    if spec is None:
        return finish("denied", {"error": f"unknown tool {name}", "code": "unknown_tool"}, error="unknown tool")
    if ctx.user.role not in spec.roles or ctx.surface not in spec.surfaces:
        audit.record(ctx.db, actor_type="agent", actor_id=ctx.user.id, action="tool.denied", target_type="tool", target_id=name, outcome="denied", details={"role": ctx.user.role, "surface": ctx.surface, "run_id": ctx.run.id}, commit=False)
        return finish("denied", {"error": "this tool is not available to the current user", "code": "forbidden"}, error="forbidden")
    if spec.tier > MAX_TIER_FOR_MODEL:
        return finish("denied", {"error": "human only action", "code": "forbidden"}, error="tier")
    try:
        params = spec.model.model_validate(arguments or {})
    except ValidationError as exc:
        return finish("error", {"error": "invalid arguments", "code": "validation", "details": exc.errors(include_url=False)[:5]}, error="validation")
    try:
        output = spec.handler(ctx, params)
    except ToolError as exc:
        return finish("error", {"error": str(exc), "code": exc.code, "safe_next_step": exc.safe_next_step}, error=str(exc))
    except Exception as exc:  # unexpected: never leak internals to the model
        ctx.db.rollback()
        return finish("error", {"error": "the tool failed unexpectedly; the action was not completed", "code": "internal"}, error=f"{type(exc).__name__}: {exc}")
    if output.get("status") == "approval_required":
        return finish("approval_required", output, approval_id=output.get("approval_id"))
    return finish("ok", output)


def _summarise_output(output: dict) -> dict:
    """Keep execution records small and free of document text."""
    summary: dict = {}
    for k, v in output.items():
        if isinstance(v, list):
            summary[k] = f"list[{len(v)}]"
        elif isinstance(v, dict):
            summary[k] = {kk: (vv if isinstance(vv, (int, float, bool, type(None))) or (isinstance(vv, str) and len(vv) < 80) else type(vv).__name__) for kk, vv in list(v.items())[:12]}
        elif isinstance(v, str):
            summary[k] = v[:160]
        else:
            summary[k] = v
    return mask_value(summary)
