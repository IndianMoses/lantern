"""Multi-agent coverage analysis: Model A (analyst), Model B (challenger) and
Model C (judge) behind one provider abstraction.

Two implementations ship:

* ``RuleBasedAnalyst`` runs offline. A wraps the deterministic gap engine;
  B derives findings by a *different* method (typed facts rather than tag
  rules) and then challenges A; C compares both against the evidence with the
  verification layers. This path is what the tests and the evidence bundle use,
  and the fallback whenever a model is unavailable.
* ``OpenAICompatibleAnalyst`` talks to any OpenAI compatible chat completions
  endpoint (vLLM, llama.cpp server, Ollama, TGI, LM Studio), which is how the
  selected open-weight models (see docs/MODELS.md) are served inside a bank
  controlled environment. Outputs are strict JSON validated with pydantic; a
  response that does not validate is retried once with the validation errors
  and then treated as a provider failure, never as a finding.

Independence: B's ``analyze`` receives the same evidence package as A and
nothing else. Only ``challenge`` (a later stage) sees A's output. The judge
is not trusted because it is the judge: its verdicts are checked against the
deterministic verification layers before anything is accepted.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from ..models import CustomerProfile, Policy, PolicyClause, PolicyFact
from ..services import gap_engine
from ..services.verification import NEED_TAGS
from .providers.base import ProviderError

log = logging.getLogger("lantern.analysts")

Role = Literal["A", "B", "C"]


# ------------------------------------------------------------------ schemas ---


class Scenario(BaseModel):
    key: str
    description: dict[str, str] = Field(default_factory=dict)  # {"en":..., "zh":...}
    loss_event: str = ""
    severity_band: Literal["minor", "moderate", "major", "unknown"] = "unknown"
    likelihood_note: str = "not estimated"  # never a number unless supportable
    relevant_clause_ids: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class CandidateFinding(BaseModel):
    key: str
    need: str
    title: dict[str, str] = Field(default_factory=dict)
    statement: dict[str, str] = Field(default_factory=dict)
    status: Literal["covered", "partial", "gap", "duplicate", "note"]
    severity: Literal["high", "medium", "low"] = "low"
    kind: Literal["policy_fact", "potential_gap", "hypothetical_scenario", "unresolved"] = "potential_gap"
    clause_ids: list[str] = Field(default_factory=list)
    policy_numbers: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    suggested_action: dict[str, str] = Field(default_factory=dict)
    triggered_by: str = ""
    scenario: Scenario | None = None
    proposed_by: list[str] = Field(default_factory=list)
    rationale: str = ""
    legacy: dict[str, Any] | None = None  # the gap engine's original dict, kept for compatibility


class AnalystOutput(BaseModel):
    role: Role
    model_id: str
    findings: list[CandidateFinding] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    usage: dict = Field(default_factory=dict)
    valid: bool = True
    error: str | None = None


class Challenge(BaseModel):
    target_key: str
    type: Literal["unsupported", "overlooked_coverage", "misinterpretation", "missing_exclusion", "incorrect_assumption", "alternative_reading", "duplicate", "needs_information"]
    note: str
    clause_ids: list[str] = Field(default_factory=list)


class ChallengeOutput(BaseModel):
    model_id: str
    challenges: list[Challenge] = Field(default_factory=list)
    additional_findings: list[CandidateFinding] = Field(default_factory=list)
    latency_ms: int = 0
    usage: dict = Field(default_factory=dict)
    valid: bool = True
    error: str | None = None


class Verdict(BaseModel):
    key: str
    verdict: Literal["accepted", "rejected", "revise", "unresolved"]
    reason: str = ""
    revised_statement: dict[str, str] | None = None
    qualification_clause_ids: list[str] = Field(default_factory=list)


class JudgeOutput(BaseModel):
    model_id: str
    verdicts: list[Verdict] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    usage: dict = Field(default_factory=dict)
    valid: bool = True
    error: str | None = None


@dataclass
class EvidencePackage:
    """Minimal, task specific evidence: never the whole profile or document library."""

    language: str
    customer: dict  # housing, household size, currency, assets (type, label, value), activities, concerns
    policies: list[dict]  # {id, number, type, period, status, excess, facts:[...], clauses:[...]}
    today: str
    analysis_scope: str = "coverage_gap_analysis"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class AnalystProvider(Protocol):
    role: Role
    model_id: str

    def analyze(self, pkg: EvidencePackage) -> AnalystOutput: ...

    def challenge(self, pkg: EvidencePackage, a_output: AnalystOutput, own_output: AnalystOutput) -> ChallengeOutput: ...

    def adjudicate(self, pkg: EvidencePackage, a_output: AnalystOutput, b_output: AnalystOutput, challenge: ChallengeOutput, verification: dict[str, dict]) -> JudgeOutput: ...


# --------------------------------------------------------- evidence package ---


def build_package(profile: CustomerProfile, policies: list[tuple[Policy, list[PolicyClause]]], facts: dict[str, list[PolicyFact]], *, language: str, today: date | None = None) -> EvidencePackage:
    today = today or date.today()
    customer = {
        "housing": profile.housing, "household": profile.household or {}, "currency": profile.currency, "country": profile.country,
        "assets": [{"index": i, "type": a.get("type"), "label": a.get("label"), "value": a.get("value"), "notes": a.get("notes")} for i, a in enumerate(profile.assets or [])],
        "activities": [{"type": a.get("type"), "label": a.get("label"), "destinations": a.get("destinations")} for a in (profile.activities or [])],
        "concerns": list(profile.concerns or []),
    }
    pols = []
    for p, clauses in policies:
        pols.append({
            "id": p.id, "number": p.policy_number, "provider": p.provider_name, "product": p.product_name, "type": p.insurance_type, "status": p.status, "version": p.version,
            "period": {"from": p.start_date.isoformat(), "to": p.end_date.isoformat()}, "excess": p.excess_amount, "currency": p.currency, "language": p.language,
            "facts": [{"id": f.id, "type": f.fact_type, "subject": f.subject, "value": f.value, "unit": f.unit, "status": f.status, "clause_id": f.clause_id} for f in facts.get(p.id, []) if f.status != "not_applicable"],
            "clauses": [{"id": c.id, "ref": c.clause_ref, "kind": c.kind, "title": c.title, "tags": c.tags, "amount": c.amount, "text": " ".join((c.text or "").split())[:600]} for c in clauses],
            "endorsements": p.endorsements or [],
        })
    return EvidencePackage(language=language, customer=customer, policies=pols, today=today.isoformat())


# ------------------------------------------------------- rule based analyst ---


def _legacy_to_candidate(f: dict, proposed_by: str) -> CandidateFinding:
    return CandidateFinding(
        key=f["need"], need=f["need"], title=f["label"], statement=f["explanation"], status=f["status"], severity=f["severity"],
        kind="policy_fact" if f["status"] == "covered" else "potential_gap" if f["status"] in {"gap", "partial", "duplicate"} else "policy_fact",
        clause_ids=[e["clause_id"] for e in f.get("evidence", [])], policy_numbers=list(f.get("policies", [])), suggested_action=f.get("suggested_action", {}),
        triggered_by=f.get("triggered_by", ""), proposed_by=[proposed_by], legacy=f,
    )


ASSET_NEEDS = {  # the challenger's own view of what each asset type needs, expressed as coverage tags
    "home": ("buildings", {"buildings"}, {"home"}),
    "contents": ("contents", {"contents"}, {"home", "renters"}),
    "car": ("motor_liability", {"third_party"}, {"motor"}),
    "bicycle": ("bicycle", {"bicycle"}, {"home", "renters"}),
    "ebike": ("ebike_liability", {"third_party"}, {"ebike"}),
    "phone": ("phone", {"phone"}, {"gadget"}),
    "laptop": ("laptop_home", {"contents", "business_equipment"}, {"home", "renters", "gadget"}),
    "pet": ("pet_vet_fees", {"vet_fees"}, {"pet"}),
    "jewellery": ("valuables", {"contents"}, {"home", "renters"}),
}
ASSET_KEYWORDS = {  # words a sub limit excerpt must mention to apply to the asset
    "bicycle": ("pedal cycle", "bicycle", "cycle", "bike"), "laptop": ("laptop", "computer", "office equipment", "business equipment", "single item", "any one item"),
    "phone": ("phone", "mobile", "gadget", "single item", "屏", "整机"), "jewellery": ("valuable", "jewellery", "jewelry", "single item", "any one item"),
    "ebike": ("theft", "整车", "盗抢"), "contents": ("contents", "sum insured", "家财"), "home": ("buildings", "rebuild"), "pet": ("vet", "veterinary", "annual limit"), "car": ("market value", "vehicle"),
}
LABELS = {
    "waiting_period_active": ("Waiting period still running", "等待期尚未结束"),
    "notification_deadline": ("Claim notification deadline", "报案时限"),
    "policy_expiring": ("Policy ends soon", "保单即将到期"),
    "policy_not_yet_in_force": ("Policy not yet in force", "保单尚未生效"),
    "pending_policy": ("Uploaded policy not confirmed", "上传的保单尚未确认"),
    "extension_not_selected": ("Optional extension not selected", "可选扩展未选择"),
    "excess_vs_value": ("Excess close to the item's value", "免赔额接近物品价值"),
    "conflicting_terms": ("Conflicting figures in the wording", "条款中的数值相互矛盾"),
}


class RuleBasedAnalyst:
    """Deterministic implementation of all three roles."""

    def __init__(self, role: Role, *, profile: CustomerProfile, policies: list[tuple[Policy, list[PolicyClause]]], facts: dict[str, list[PolicyFact]], today: date | None = None):
        self.role = role
        self.model_id = f"rules-{role.lower()}@1"
        self.profile = profile
        self.policies = policies
        self.facts = facts
        self.today = today or date.today()
        self.clauses = {c.id: c for _, cls in policies for c in cls}
        self.by_number = {p.policy_number: p for p, _ in policies}

    # ---- Model A: the gap engine as the primary analyst
    def analyze(self, pkg: EvidencePackage) -> AnalystOutput:
        t0 = time.perf_counter()
        if self.role == "A":
            result = gap_engine.analyze(self.profile, self.policies)
            findings = [_legacy_to_candidate(f, "A") for f in result["findings"]]
            for f in findings:
                f.scenario = self._scenario_for(f)
            return AnalystOutput(role="A", model_id=self.model_id, findings=findings, scenarios=[f.scenario for f in findings if f.scenario], latency_ms=int((time.perf_counter() - t0) * 1000))
        return AnalystOutput(role=self.role, model_id=self.model_id, findings=self._challenger_findings(), latency_ms=int((time.perf_counter() - t0) * 1000))

    def _scenario_for(self, f: CandidateFinding) -> Scenario | None:
        if f.status not in {"gap", "partial", "duplicate"}:
            return None
        text = {
            "en": f"If the risk behind '{f.title.get('en', f.need)}' materialised today, the part of the loss that falls outside the cited clauses would be yours to carry. Figures are only shown where the wording states them.",
            "zh": f"如果“{f.title.get('zh', f.need)}”所涉风险今天发生，超出所引条款的损失部分需由你自行承担。金额仅在条款明确写明时显示。",
        }
        return Scenario(key=f"scenario:{f.need}", description=text, loss_event=f.need, severity_band={"high": "major", "medium": "moderate", "low": "minor"}.get(f.severity, "unknown"), relevant_clause_ids=f.clause_ids)

    # ---- Model B: an independent, fact based reading
    def _challenger_findings(self) -> list[CandidateFinding]:
        out: list[CandidateFinding] = []
        active = [(p, c) for p, c in self.policies if p.status == "active"]
        cur = self.profile.currency or "GBP"

        def cov_clauses(tags: set[str], types: set[str]) -> list[PolicyClause]:
            hits = []
            for p, cls in active:
                if p.insurance_type not in types:
                    continue
                for c in cls:
                    if c.kind == "coverage" and "not_selected" not in (c.tags or []) and (set(c.tags or []) & tags):
                        hits.append(c)
            return hits

        # 1. asset needs re-derived from coverage facts (independent of the tag rules in the gap engine)
        for a in self.profile.assets or []:
            spec = ASSET_NEEDS.get(a.get("type"))
            if not spec:
                continue
            need, tags, types = spec
            hits = cov_clauses(tags, types)
            value = float(a.get("value") or 0)
            if not hits:
                out.append(CandidateFinding(key=need, need=need, title={"en": f"No cover found for {a.get('label', a.get('type'))}", "zh": f"未找到对{a.get('label', a.get('type'))}的保障"}, statement={"en": f"Reading the typed facts of every active policy, no coverage fact addresses {a.get('label', a.get('type'))}.", "zh": f"对照所有有效保单的结构化事实，没有保障事实涉及{a.get('label', a.get('type'))}。"}, status="gap", severity="high" if value >= 5000 or a.get("type") in {"home", "car", "ebike"} else "medium", triggered_by=a.get("label", ""), proposed_by=["B"], rationale="absence of a coverage fact with the asset's tags across active policies", suggested_action={"en": "Confirm with the insurer or add the policy that covers it.", "zh": "向保险公司确认，或添加承保该项的保单。"}))
                continue
            # limit below value from facts, using only amounts whose excerpt refers to this kind of asset
            words = ASSET_KEYWORDS.get(a.get("type"), ())
            limits = [f for c in hits for f in self.facts.get(c.policy_id, []) if f.clause_id == c.id and f.fact_type in {"limit", "sub_limit"} and f.value and any(w in (f.text_excerpt or "").lower() for w in words)]
            cap = min((f.value for f in limits), default=None)
            if cap and value and value > cap:
                c0 = next(c for c in hits if any(f.clause_id == c.id for f in limits))
                out.append(CandidateFinding(key=need, need=need, title={"en": f"{a.get('label', a.get('type'))}: stated limit below value", "zh": f"{a.get('label', a.get('type'))}：限额低于价值"}, statement={"en": f"The wording states a limit of {cap:,.0f} {cur} that applies to {a.get('label', a.get('type')).lower()} valued at {value:,.0f} {cur}.", "zh": f"条款写明适用于{a.get('label', a.get('type'))}的限额为 {cap:,.0f} {cur}，而其申报价值为 {value:,.0f} {cur}。"}, status="partial", severity="high" if value - cap >= 1000 else "medium", clause_ids=[c0.id], policy_numbers=[c0.policy.policy_number], triggered_by=a.get("label", ""), proposed_by=["B"], rationale="limit fact below declared value", suggested_action={"en": "Ask whether the item can be specified or the limit raised.", "zh": "询问能否单独列明该物品或提高限额。"}))
            else:
                c0 = hits[0]
                out.append(CandidateFinding(key=need, need=need, title={"en": f"{a.get('label', a.get('type'))}: a coverage clause applies", "zh": f"{a.get('label', a.get('type'))}：有保障条款适用"}, statement={"en": f"At least one coverage clause addresses {a.get('label', a.get('type')).lower()} ({c0.policy.policy_number} §{c0.clause_ref}).", "zh": f"至少一条保障条款涉及{a.get('label', a.get('type'))}（{c0.policy.policy_number} §{c0.clause_ref}）。"}, status="covered", severity="low", kind="policy_fact", clause_ids=[c0.id], policy_numbers=[c0.policy.policy_number], triggered_by=a.get("label", ""), proposed_by=["B"], rationale="coverage fact present"))

        # 2. waiting periods still running on recently started policies
        for p, _ in active:
            for f in self.facts.get(p.id, []):
                if f.fact_type == "waiting_period" and f.value and f.unit in {"days", "hours"} and f.clause_id:
                    days = f.value / 24 if f.unit == "hours" else f.value
                    until = p.start_date + timedelta(days=int(days))
                    if self.today < until and (self.today - p.start_date).days >= 0 and days <= 120:
                        out.append(CandidateFinding(key=f"waiting_period_active:{p.policy_number}", need="waiting_period_active", title={"en": LABELS["waiting_period_active"][0], "zh": LABELS["waiting_period_active"][1]}, statement={"en": f"{p.policy_number} states a waiting period of {int(days)} days for {f.subject.replace('_', ' ')}; it runs until {until.isoformat()}.", "zh": f"{p.policy_number} 对{f.subject.replace('_', ' ')}设有 {int(days)} 天等待期，至 {until.isoformat()} 结束。"}, status="note", severity="medium", kind="policy_fact", clause_ids=[f.clause_id], policy_numbers=[p.policy_number], triggered_by="Recently started policy", proposed_by=["B"], rationale="waiting_period fact and policy start date", suggested_action={"en": "Until then a claim in this category may be declined; keep evidence of when any condition first appeared.", "zh": "在此之前该类理赔可能被拒；保留病症或事件首次出现时间的证据。"}))
                        break

        # 3. notification deadlines (conditions the customer may find hard to satisfy), one note for the portfolio
        rows_en, rows_zh, cids, pns = [], [], [], []
        for p, _ in active:
            deadlines = [f for f in self.facts.get(p.id, []) if f.fact_type == "notification_deadline" and f.value and f.clause_id]
            if deadlines:
                f = min(deadlines, key=lambda x: (x.value if x.unit == "hours" else x.value * 24))
                unit = "hours" if f.unit == "hours" else "days"
                rows_en.append(f"{p.policy_number}: within {int(f.value)} {unit} (§{self.clauses[f.clause_id].clause_ref})")
                rows_zh.append(f"{p.policy_number}：{int(f.value)} {'小时' if unit == 'hours' else '天'}内（§{self.clauses[f.clause_id].clause_ref}）")
                cids.append(f.clause_id)
                pns.append(p.policy_number)
        if rows_en:
            out.append(CandidateFinding(key="notification_deadline", need="notification_deadline", title={"en": LABELS["notification_deadline"][0], "zh": LABELS["notification_deadline"][1]}, statement={"en": "Each policy sets a time limit for telling the insurer about an incident; missing it can prejudice a claim. " + "; ".join(rows_en) + ".", "zh": "每份保单都规定了报案时限，逾期可能影响理赔。" + "；".join(rows_zh) + "。"}, status="note", severity="low", kind="policy_fact", clause_ids=cids, policy_numbers=pns, triggered_by="Claims conditions", proposed_by=["B"], rationale="notification_deadline facts", suggested_action={"en": "Report any incident straight away, even before the paperwork is ready.", "zh": "发生事故后立即报案，材料可随后补充。"}))

        # 4. expiry and lapse risk, policies not yet in force, uploads not confirmed
        for p, _ in self.policies:
            days = (p.end_date - self.today).days
            if p.status == "active" and 0 <= days <= 45:
                cf = CandidateFinding(key=f"policy_expiring:{p.policy_number}", need="policy_expiring", title={"en": LABELS["policy_expiring"][0], "zh": LABELS["policy_expiring"][1]}, statement={"en": f"{p.provider_name} {p.product_name} ({p.policy_number}) ends on {p.end_date.isoformat()}, in {days} days. Cover lapses if it is not renewed or replaced.", "zh": f"{p.provider_name}{p.product_name}（{p.policy_number}）将于 {p.end_date.isoformat()} 到期（{days} 天后）。不续保或替换将失去保障。"}, status="note", severity="medium", kind="policy_fact", policy_numbers=[p.policy_number], triggered_by="Policy period", proposed_by=["B"], rationale="policy end date", suggested_action={"en": "Decide before the end date whether to renew, replace or let it lapse deliberately.", "zh": "在到期前决定续保、替换或有意放弃。"})
                cf.legacy = None
                cf.assumptions = []
                out.append(cf)
                setattr(cf, "_derived", [days])
            if p.status == "active" and p.start_date > self.today:
                out.append(CandidateFinding(key=f"policy_not_yet_in_force:{p.policy_number}", need="policy_not_yet_in_force", title={"en": LABELS["policy_not_yet_in_force"][0], "zh": LABELS["policy_not_yet_in_force"][1]}, statement={"en": f"{p.policy_number} starts on {p.start_date.isoformat()}; until then it provides no cover.", "zh": f"{p.policy_number} 自 {p.start_date.isoformat()} 起生效，在此之前不提供保障。"}, status="note", severity="low", kind="policy_fact", policy_numbers=[p.policy_number], triggered_by="Policy period", proposed_by=["B"], rationale="start date in the future"))
            if p.status == "pending_review":
                out.append(CandidateFinding(key=f"pending_policy:{p.policy_number}", need="pending_policy", title={"en": LABELS["pending_policy"][0], "zh": LABELS["pending_policy"][1]}, statement={"en": f"{p.policy_number} was extracted from your upload and is not confirmed, so it was left out of this analysis.", "zh": f"{p.policy_number} 来自你上传的文件且尚未确认，因此未纳入本次分析。"}, status="note", severity="low", kind="policy_fact", policy_numbers=[p.policy_number], triggered_by="Uploaded policy", proposed_by=["B"], rationale="pending_review status", missing_information=["Confirm the extracted details to include the policy"], suggested_action={"en": "Check the extracted details and confirm the policy.", "zh": "核对提取的信息并确认该保单。"}))

        # 5. optional extensions not selected that match the customer's circumstances
        act_types = {a.get("type") for a in (self.profile.activities or [])}
        for p, _ in active:
            for f in self.facts.get(p.id, []):
                if f.fact_type == "extension_not_selected" and f.clause_id:
                    tags = set(f.tags)
                    relevant = ("away_from_home" in tags and act_types & {"hybrid_work", "travel", "cycling_commute"}) or ("accidental_damage" in tags and (self.profile.household or {}).get("children"))
                    if relevant:
                        c = self.clauses[f.clause_id]
                        out.append(CandidateFinding(key=f"extension_not_selected:{p.policy_number}:{c.clause_ref}", need="extension_not_selected", title={"en": LABELS["extension_not_selected"][0], "zh": LABELS["extension_not_selected"][1]}, statement={"en": f"{p.policy_number} §{c.clause_ref} ({c.title}) is an optional extension that is not selected, although your circumstances suggest it is relevant.", "zh": f"{p.policy_number} §{c.clause_ref}（{c.title}）是未选择的可选扩展，但你的情况显示它可能相关。"}, status="partial", severity="medium", clause_ids=[c.id], policy_numbers=[p.policy_number], triggered_by="Your activities", proposed_by=["B"], rationale="extension_not_selected fact matched to activities", suggested_action={"en": "Ask the insurer for the price of adding the extension.", "zh": "询问保险公司加保该扩展的费用。"}))

        # 6. conflicting figures inside one wording
        for p, _ in active:
            conflicts = [f for f in self.facts.get(p.id, []) if f.status == "conflicting"]
            if conflicts:
                f = conflicts[0]
                out.append(CandidateFinding(key=f"conflicting_terms:{p.policy_number}:{f.fact_type}", need="conflicting_terms", title={"en": LABELS["conflicting_terms"][0], "zh": LABELS["conflicting_terms"][1]}, statement={"en": f.ambiguity or "Conflicting figures were found.", "zh": "同一保单中对同一事项写有不同数值，需向保险公司确认适用哪一个。"}, status="note", severity="medium", kind="unresolved", clause_ids=[x.clause_id for x in conflicts if x.clause_id][:3], policy_numbers=[p.policy_number], triggered_by="Wording", proposed_by=["B"], missing_information=["Which figure applies to which section"], rationale="conflicting fact status"))
        return out

    # ---- Model B: challenge A's findings after forming its own view
    def challenge(self, pkg: EvidencePackage, a_output: AnalystOutput, own_output: AnalystOutput) -> ChallengeOutput:
        t0 = time.perf_counter()
        challenges: list[Challenge] = []
        own_by_need: dict[str, CandidateFinding] = {}
        for f in own_output.findings:
            own_by_need.setdefault(f.need, f)
        seen_needs: dict[str, int] = {}
        for f in a_output.findings:
            seen_needs[f.need] = seen_needs.get(f.need, 0) + 1
            need_tags = NEED_TAGS.get(f.need, set())
            # unsupported: cited clause does not share a tag with the need
            for cid in f.clause_ids:
                c = self.clauses.get(cid)
                if c is None:
                    challenges.append(Challenge(target_key=f.key, type="unsupported", note=f"cited clause {cid} does not exist", clause_ids=[cid]))
                elif need_tags and not (set(c.tags or []) & need_tags) and c.kind == "coverage":
                    challenges.append(Challenge(target_key=f.key, type="unsupported", note=f"{c.policy.policy_number} §{c.clause_ref} does not share any tag with the need '{f.need}'", clause_ids=[cid]))
            # missing exclusion: an exclusion in the cited policy touches the need but was not cited
            if f.status == "covered":
                for pn in f.policy_numbers:
                    p = self.by_number.get(pn)
                    if not p:
                        continue
                    for c in p.clauses:
                        if c.kind == "exclusion" and c.id not in f.clause_ids and need_tags and (set(c.tags or []) & need_tags):
                            challenges.append(Challenge(target_key=f.key, type="missing_exclusion", note=f"{pn} §{c.clause_ref} ({c.title}) may narrow this cover and is not cited", clause_ids=[c.id]))
            # disagreement with the independent reading
            mine = own_by_need.get(f.need)
            if mine and mine.status != f.status and {mine.status, f.status} & {"gap"}:
                challenges.append(Challenge(target_key=f.key, type="alternative_reading", note=f"independent fact based reading gives '{mine.status}' rather than '{f.status}'", clause_ids=mine.clause_ids))
            if f.status in {"gap", "partial"} and not f.clause_ids and f.status == "partial":
                challenges.append(Challenge(target_key=f.key, type="needs_information", note="a partial finding without a cited clause needs the limiting clause", clause_ids=[]))
        for need, n in seen_needs.items():
            if n > 1:
                challenges.append(Challenge(target_key=need, type="duplicate", note=f"{n} findings share the need '{need}'"))
        additional = [f for f in own_output.findings if f.need not in seen_needs]
        return ChallengeOutput(model_id=self.model_id, challenges=challenges, additional_findings=additional, latency_ms=int((time.perf_counter() - t0) * 1000))

    # ---- Model C: adjudicate against evidence and the verification layers
    def adjudicate(self, pkg: EvidencePackage, a_output: AnalystOutput, b_output: AnalystOutput, challenge: ChallengeOutput, verification: dict[str, dict]) -> JudgeOutput:
        t0 = time.perf_counter()
        verdicts: list[Verdict] = []
        missing: list[str] = []
        by_key = {c.target_key: [] for c in challenge.challenges}
        for c in challenge.challenges:
            by_key[c.target_key].append(c)
        b_by_need = {f.need: f for f in b_output.findings}
        candidates = list(a_output.findings) + list(challenge.additional_findings)
        for f in candidates:
            v = verification.get(f.key, {})
            chals = by_key.get(f.key, [])
            if v and not v.get("source_exists", True):
                verdicts.append(Verdict(key=f.key, verdict="rejected", reason="a cited clause could not be found in the customer's policies"))
                continue
            if any(c.type == "unsupported" for c in chals) and f.status in {"covered", "partial"}:
                verdicts.append(Verdict(key=f.key, verdict="rejected", reason="the cited clause does not bear on the need"))
                continue
            alt = [c for c in chals if c.type == "alternative_reading"]
            if alt:
                b = b_by_need.get(f.need)
                # evidence decides: the reading that cites an existing clause bearing on the need wins; otherwise unresolved
                a_supported = v.get("evidence_support") in {"supported", "supports_with_qualification"}
                b_cites = bool(b and b.clause_ids)
                if a_supported and not b_cites:
                    verdicts.append(Verdict(key=f.key, verdict="accepted", reason="the analyst's reading cites a clause that bears on the need; the challenger's reading cites none"))
                elif b_cites and not a_supported:
                    verdicts.append(Verdict(key=f.key, verdict="revise", reason="the challenger's reading is the one supported by a cited clause", qualification_clause_ids=b.clause_ids if b else []))
                else:
                    verdicts.append(Verdict(key=f.key, verdict="unresolved", reason=alt[0].note))
                    missing.append(f"{f.need}: the two readings disagree and the wording does not settle it")
                continue
            excl = [c for c in chals if c.type == "missing_exclusion"] or [x for x in v.get("contradictions", []) if x.get("kind") == "exclusion"]
            if excl and f.status == "covered":
                ids = [cid for c in chals if c.type == "missing_exclusion" for cid in c.clause_ids] or [x.get("clause_id") for x in v.get("contradictions", []) if x.get("kind") == "exclusion" and x.get("clause_id")]
                verdicts.append(Verdict(key=f.key, verdict="revise", reason="an exclusion in the same policy touches this cover and must be shown alongside it", qualification_clause_ids=ids))
                continue
            if v.get("rule_consistency") == "mismatch":
                verdicts.append(Verdict(key=f.key, verdict="revise", reason=f"figures {v.get('unmatched_figures')} do not trace to a record; they are shown as unverified"))
                continue
            if f.kind == "unresolved" or f.missing_information and f.status in {"gap", "partial"} and not f.clause_ids and f.need not in {"pending_policy"}:
                verdicts.append(Verdict(key=f.key, verdict="unresolved", reason="; ".join(f.missing_information) or "information needed"))
                continue
            verdicts.append(Verdict(key=f.key, verdict="accepted", reason="evidence exists, bears on the need and no contradiction was found" if f.clause_ids else "confirmed absence across the active portfolio" if f.status == "gap" else "policy fact"))
        return JudgeOutput(model_id=self.model_id, verdicts=verdicts, missing_evidence=missing, next_steps=["Confirm unresolved items with the insurer before relying on them."] if missing else [], latency_ms=int((time.perf_counter() - t0) * 1000))


# ------------------------------------------------- OpenAI compatible models ---


SYSTEM_A = """You are Model A, the primary insurance analyst in a bank's insurance intelligence platform.
You receive a JSON evidence package: a customer's circumstances and their policies, each with typed facts and clauses (id, ref, kind, tags, text).
Produce candidate coverage findings as strict JSON matching the schema below. Rules:
- Every statement about cover must cite clause ids from the package. No clause id, no policy specific claim.
- Never invent clause numbers, amounts, probabilities or outcomes. If the wording is silent say so with status "gap" and an empty clause list, and name the missing information.
- Distinguish facts the wording states from your interpretation; put interpretations in "assumptions".
- Statuses: covered | partial | gap | duplicate | note. Severity: high | medium | low.
- Write "title", "statement" and "suggested_action" in both languages: {"en": ..., "zh": ...}.
- Do not recommend buying any product. Suggested actions are questions for the insurer or checks the customer can make.
Output: {"findings":[{"key":str,"need":str,"title":{"en":str,"zh":str},"statement":{"en":str,"zh":str},"status":str,"severity":str,"kind":"policy_fact|potential_gap|hypothetical_scenario|unresolved","clause_ids":[str],"policy_numbers":[str],"assumptions":[str],"missing_information":[str],"suggested_action":{"en":str,"zh":str},"triggered_by":str,"rationale":str}],"scenarios":[{"key":str,"description":{"en":str,"zh":str},"loss_event":str,"severity_band":"minor|moderate|major|unknown","likelihood_note":str,"relevant_clause_ids":[str],"missing_information":[str]}],"missing_information":[str],"notes":[str]}
Return only the JSON object."""

SYSTEM_B = """You are Model B, an independent challenger in a bank's insurance intelligence platform. You have NOT seen any other analyst's output.
Analyse the same JSON evidence package from scratch and produce your own candidate findings using the same schema as the primary analyst (findings, scenarios, missing_information, notes), citing clause ids only from the package.
Look especially for overlooked exclusions, conditions, waiting periods, notification deadlines, territorial limits, optional extensions not selected, policies about to expire, and limits below declared values. Never invent figures. Return only the JSON object."""

SYSTEM_B_CHALLENGE = """You are Model B. You have already produced your own independent assessment (provided). Now review the primary analyst's findings against the evidence package.
For each finding report challenges as strict JSON: {"challenges":[{"target_key":str,"type":"unsupported|overlooked_coverage|misinterpretation|missing_exclusion|incorrect_assumption|alternative_reading|duplicate|needs_information","note":str,"clause_ids":[str]}],"additional_findings":[...same finding schema...]}
Only cite clause ids from the package. Return only the JSON object."""

SYSTEM_C = """You are Model C, the judge in a bank's insurance intelligence platform. You receive the evidence package, the findings of two independent analysts, the challenger's challenges and the result of deterministic verification checks for each finding key.
Decide for every finding key: accepted | rejected | revise | unresolved, with a reason that refers to clause ids or verification results. Agreement between the analysts is not evidence; the clauses are. If the wording does not settle a disagreement, return unresolved rather than certainty.
Output strict JSON: {"verdicts":[{"key":str,"verdict":str,"reason":str,"revised_statement":{"en":str,"zh":str}|null,"qualification_clause_ids":[str]}],"missing_evidence":[str],"next_steps":[str]}
Return only the JSON object."""


def _extract_json(text: str) -> dict:
    # hybrid reasoning models (Qwen3, GLM-4.5) may emit a <think> block even
    # when asked not to; it is never part of the answer
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in response")
    return json.loads(text[start : end + 1])


class OpenAICompatibleAnalyst:
    """Any OpenAI compatible chat completions server. Used to run the selected
    open-weight models (docs/MODELS.md) on infrastructure the bank controls."""

    def __init__(self, role: Role, *, base_url: str, model: str, api_key: str | None = None, timeout: float = 60.0, max_retries: int = 1, max_tokens: int = 3000, temperature: float = 0.1):
        self.role = role
        self.model_id = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.temperature = temperature

    def _chat(self, system: str, user: str, schema_model: type[BaseModel]) -> tuple[dict, dict, int]:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error: Exception | None = None
        t0 = time.perf_counter()
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    # chat_template_kwargs turns off the thinking mode of Qwen3 and
                    # GLM-4.5 on vLLM and SGLang (ignored by servers that do not know it)
                    r = client.post(f"{self.base_url}/chat/completions", headers=headers, json={"model": self.model_id, "messages": messages, "temperature": self.temperature, "max_tokens": self.max_tokens, "response_format": {"type": "json_object"}, "chat_template_kwargs": {"enable_thinking": False}})
                r.raise_for_status()
                body = r.json()
                content = body["choices"][0]["message"]["content"]
                data = _extract_json(content)
                schema_model.model_validate(data)
                usage = body.get("usage", {})
                return data, usage, int((time.perf_counter() - t0) * 1000)
            except (ValidationError, ValueError, KeyError) as exc:
                last_error = exc
                messages.append({"role": "user", "content": f"Your previous answer did not validate: {str(exc)[:800]}. Return only a corrected JSON object."})
            except Exception as exc:  # network, HTTP, timeout
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
        raise ProviderError(f"{self.role}:{self.model_id}: {last_error}")

    def analyze(self, pkg: EvidencePackage) -> AnalystOutput:
        class _Out(BaseModel):
            findings: list[CandidateFinding]
            scenarios: list[Scenario] = Field(default_factory=list)
            missing_information: list[str] = Field(default_factory=list)
            notes: list[str] = Field(default_factory=list)

        data, usage, ms = self._chat(SYSTEM_A if self.role == "A" else SYSTEM_B, pkg.to_json(), _Out)
        out = _Out.model_validate(data)
        for f in out.findings:
            f.proposed_by = [self.role]
        return AnalystOutput(role=self.role, model_id=self.model_id, findings=out.findings, scenarios=out.scenarios, missing_information=out.missing_information, notes=out.notes, latency_ms=ms, usage=usage)

    def challenge(self, pkg: EvidencePackage, a_output: AnalystOutput, own_output: AnalystOutput) -> ChallengeOutput:
        class _Out(BaseModel):
            challenges: list[Challenge] = Field(default_factory=list)
            additional_findings: list[CandidateFinding] = Field(default_factory=list)

        user = json.dumps({"package": asdict(pkg), "your_assessment": own_output.model_dump(), "primary_analyst": a_output.model_dump()}, ensure_ascii=False)
        data, usage, ms = self._chat(SYSTEM_B_CHALLENGE, user, _Out)
        out = _Out.model_validate(data)
        for f in out.additional_findings:
            f.proposed_by = ["B"]
        return ChallengeOutput(model_id=self.model_id, challenges=out.challenges, additional_findings=out.additional_findings, latency_ms=ms, usage=usage)

    def adjudicate(self, pkg: EvidencePackage, a_output: AnalystOutput, b_output: AnalystOutput, challenge: ChallengeOutput, verification: dict[str, dict]) -> JudgeOutput:
        class _Out(BaseModel):
            verdicts: list[Verdict] = Field(default_factory=list)
            missing_evidence: list[str] = Field(default_factory=list)
            next_steps: list[str] = Field(default_factory=list)

        user = json.dumps({"package": asdict(pkg), "analyst_a": a_output.model_dump(), "analyst_b": b_output.model_dump(), "challenge": challenge.model_dump(), "verification": verification}, ensure_ascii=False)
        data, usage, ms = self._chat(SYSTEM_C, user, _Out)
        out = _Out.model_validate(data)
        return JudgeOutput(model_id=self.model_id, verdicts=out.verdicts, missing_evidence=out.missing_evidence, next_steps=out.next_steps, latency_ms=ms, usage=usage)
