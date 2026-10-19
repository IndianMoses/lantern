"""Evidence verification for coverage findings (independent of any model).

Six layers, each recorded separately so a reviewer can see which one failed:

A  source_exists      the cited clauses exist, belong to the cited policies and
                      the policies belong to this customer
B  evidence_support   each cited clause actually bears on the need (shared
                      tags, or an amount quoted from it), not merely the topic
C  contradictions     exclusions, conditions, definitions or endorsements in
                      the same policies that touch the need but were not cited
D  rule_consistency   every figure in the statement traces to a stored fact,
                      a cited clause or a value the customer declared
E  independent_check  the judge's verdict (Model C or the rule based judge)
F  escalation         impact against reliability: does this need a person?

An unverified citation never becomes verified by repetition: the verifier
only looks at records, never at the text of another finding.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Policy, PolicyClause, PolicyFact

NEED_TAGS: dict[str, set[str]] = {
    "buildings": {"buildings"}, "contents": {"contents"}, "tenants_liability": {"tenants_liability", "pet_damage"},
    "motor_liability": {"third_party", "liability"}, "motor_delivery_use": {"delivery_use", "hire_or_reward"}, "motor_own_damage": {"own_damage", "vehicle", "accidental_damage"},
    "ebike_liability": {"third_party"}, "ebike_theft": {"theft"}, "ebike_rider_accident": {"personal_accident"}, "battery_fire": {"battery", "charging", "fire", "contents"}, "ebike_delivery_use": {"delivery_use", "declared_use"},
    "bicycle": {"bicycle", "away_from_home"}, "business_equipment": {"business_equipment"}, "laptop_away": {"personal_possessions", "away_from_home", "laptop"}, "laptop_home": {"contents", "laptop"},
    "phone": {"phone", "theft", "personal_possessions", "gadget", "away_from_home", "screen"}, "valuables": {"single_item_limit", "valuables", "contents", "jewellery"},
    "pet_vet_fees": {"vet_fees"}, "pet_liability": {"liability", "dog"}, "travel": {"medical"}, "travel_duplicate": {"medical", "cancellation", "baggage"}, "travel_usa": {"excl_usa_canada", "usa", "worldwide", "geographic_scope"}, "travel_medical": {"medical"},
    "protection_note": set(),
    "waiting_period_active": {"waiting_period"}, "notification_deadline": {"time_limit", "claims_procedure"}, "policy_expiring": set(), "excess_vs_value": {"excess"},
    "territorial_limit": {"geographic_scope"}, "pending_policy": set(), "extension_not_selected": {"not_selected"}, "policy_not_yet_in_force": set(), "conflicting_terms": set(),
}
# product types that can address a need; used by the absence check so a pet policy's
# "medical" tag is not read as travel medical cover
NEED_TYPES: dict[str, set[str]] = {
    "travel": {"travel"}, "travel_medical": {"travel"}, "travel_usa": {"travel"}, "travel_duplicate": {"travel"},
    "motor_liability": {"motor"}, "motor_own_damage": {"motor"}, "motor_delivery_use": {"motor"},
    "ebike_liability": {"ebike"}, "ebike_theft": {"ebike"}, "ebike_rider_accident": {"ebike"},
    "pet_vet_fees": {"pet"}, "pet_liability": {"pet"}, "buildings": {"home"}, "contents": {"home", "renters"}, "tenants_liability": {"renters"},
    "bicycle": {"home", "renters"}, "valuables": {"home", "renters"}, "business_equipment": {"home", "renters"}, "laptop_away": {"home", "renters", "gadget", "travel"}, "laptop_home": {"home", "renters", "gadget"},
    "phone": {"gadget", "home", "renters", "travel"}, "battery_fire": {"ebike", "home", "renters"},
}
SUPPORT_RANK = {"supported": 3, "supports_with_qualification": 2, "weak": 1, "unsupported": 0}
# figures that make a checkable claim: money (currency marker before or after) or durations (unit after)
FIGURE = re.compile(r"(?:(?:£|¥|￥|RMB|GBP|CNY|HKD|USD)\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)|(?:(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s?(?:GBP|CNY|HKD|USD|元|days?|hours?|天|日|小时|working days?))", re.I)
NUMBER = re.compile(r"(?<![\w.-])(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w.-])")


@dataclass
class Context:
    user_id: str
    policies: dict[str, Policy]  # by policy_number
    clauses: dict[str, PolicyClause]  # by clause id
    facts: dict[str, list[PolicyFact]]  # by policy id
    declared_values: set[float]
    policy_numbers_by_id: dict[str, str]


def build_context(user_id: str, policies: list[tuple[Policy, list[PolicyClause]]], facts: dict[str, list[PolicyFact]], declared_values: list[float]) -> Context:
    pol = {p.policy_number: p for p, _ in policies}
    cl: dict[str, PolicyClause] = {}
    for p, clauses in policies:
        for c in clauses:
            cl[c.id] = c
    return Context(user_id=user_id, policies=pol, clauses=cl, facts=facts, declared_values={float(v) for v in declared_values if v is not None}, policy_numbers_by_id={p.id: p.policy_number for p, _ in policies})


def _numbers(text: str) -> list[float]:
    out = []
    for m in NUMBER.finditer(text or ""):
        try:
            out.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return out


def _figures(text: str) -> list[float]:
    """Money amounts and durations stated in a sentence (identifiers, clause
    references and dates are not claims about the policy and are skipped)."""
    out = []
    for m in FIGURE.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        try:
            out.append(float(raw.replace(",", "")))
        except (ValueError, AttributeError):
            pass
    return out


def _fact_values(ctx: Context, policy_ids: set[str]) -> set[float]:
    vals: set[float] = set()
    for pid in policy_ids:
        for f in ctx.facts.get(pid, []):
            if f.value is not None:
                vals.add(float(f.value))
    return vals


def verify(finding: dict, ctx: Context, *, judge: dict | None = None, calculation: dict | None = None) -> dict:
    need = finding.get("need", "")
    need_tags = NEED_TAGS.get(need, set())
    evidence = finding.get("evidence", []) or []
    status = finding.get("status", "note")
    result: dict = {"schema_valid": True, "source_exists": True, "evidence_support": "unsupported", "evidence": [], "contradictions": [], "rule_consistency": "passed", "unmatched_figures": [], "independent_check": "pending", "escalation": [], "overall": "qualified"}

    # A: source existence and ownership
    cited_policy_ids: set[str] = set()
    for e in evidence:
        cl = ctx.clauses.get(e.get("clause_id", ""))
        ok = cl is not None and cl.policy.user_id == ctx.user_id and cl.policy.policy_number == e.get("policy_number")
        if not ok:
            result["source_exists"] = False
            result["evidence"].append({"citation": e.get("citation"), "exists": False, "support": "unsupported"})
            continue
        cited_policy_ids.add(cl.policy_id)
        tags = set(cl.tags or [])
        if need_tags and tags & need_tags:
            support = "supported"
        elif cl.kind in {"exclusion", "condition", "definition", "limit", "claims_procedure"} and (e.get("note") or cl.amount):
            support = "supports_with_qualification"
        elif need_tags:
            support = "weak"
        else:
            support = "supports_with_qualification"
        result["evidence"].append({"citation": e.get("citation"), "clause_id": cl.id, "exists": True, "kind": cl.kind, "support": support, "relationship": "supports" if support == "supported" else "supports_with_qualification" if support == "supports_with_qualification" else "related_only", "quoted_text": " ".join((cl.text or "").split())[:240]})

    # B: overall support
    supports = [x["support"] for x in result["evidence"] if x.get("exists")]
    if supports:
        result["evidence_support"] = min(supports, key=lambda s: SUPPORT_RANK[s])
    elif status == "gap" or status == "note":
        # absence findings: confirm nothing in the active portfolio addresses the need
        types = NEED_TYPES.get(need)
        addressed = [c for c in ctx.clauses.values() if c.kind == "coverage" and "not_selected" not in (c.tags or []) and c.policy.status == "active" and (not types or c.policy.insurance_type in types) and need_tags and (set(c.tags or []) & need_tags)]
        result["evidence_support"] = "supports_with_qualification" if not addressed else "unsupported"
        result["absence_check"] = {"policies_scanned": len({c.policy_id for c in ctx.clauses.values()}), "coverage_clauses_matching_need": [f"{c.policy.policy_number} §{c.clause_ref}" for c in addressed[:5]]}
    else:
        result["evidence_support"] = "unsupported"

    # C: contradiction candidates in the cited policies
    cited_clause_ids = {x.get("clause_id") for x in result["evidence"]}
    for pid in cited_policy_ids:
        for c in ctx.clauses.values():
            if c.policy_id != pid or c.id in cited_clause_ids or c.kind not in {"exclusion", "condition", "definition"}:
                continue
            if need_tags and (set(c.tags or []) & need_tags):
                result["contradictions"].append({"citation": f"{c.policy.policy_number} §{c.clause_ref}", "clause_id": c.id, "kind": c.kind, "title": c.title, "effect": "may narrow or exclude" if c.kind == "exclusion" else "adds a condition or definition"})
    # endorsements recorded on the policy that override a cited clause
    for pid in cited_policy_ids:
        pol = next((p for p in ctx.policies.values() if p.id == pid), None)
        for end in (pol.endorsements if pol else []) or []:
            if any(x.get("clause_id") and ctx.clauses[x["clause_id"]].clause_ref == end.get("overrides_clause_ref") for x in result["evidence"] if x.get("exists")):
                result["contradictions"].append({"citation": f"{pol.policy_number} endorsement {end.get('ref')}", "kind": "endorsement", "title": end.get("title"), "effect": "overrides the cited clause"})

    # D: every figure in the statement must trace to a fact, a cited clause or a declared value
    text = (finding.get("explanation") or {}).get("en", "") if isinstance(finding.get("explanation"), dict) else str(finding.get("explanation", ""))
    figures = [n for n in _figures(text) if n >= 10]
    allowed = _fact_values(ctx, cited_policy_ids) | ctx.declared_values | {float(x) for x in finding.get("derived_figures", []) or []}
    for x in result["evidence"]:
        if x.get("exists"):
            allowed |= set(_numbers(ctx.clauses[x["clause_id"]].text or ""))
    if calculation:
        allowed |= {v for v in (calculation.get("payable"), calculation.get("uncovered"), calculation.get("inputs", {}).get("eligible_loss")) if isinstance(v, (int, float))}
    unmatched = [n for n in figures if n not in allowed and not any(abs(n - a) < 0.5 for a in allowed)]
    result["unmatched_figures"] = unmatched
    result["rule_consistency"] = "passed" if not unmatched else "mismatch"

    # E: independent check (judge)
    if judge:
        result["independent_check"] = judge.get("verdict", "pending")
        if judge.get("reason"):
            result["independent_reason"] = judge["reason"]

    # F: risk based escalation
    sev = finding.get("severity", "low")
    if result["independent_check"] == "unresolved":
        result["escalation"].append("models disagree and the wording does not settle it")
    if not result["source_exists"]:
        result["escalation"].append("a cited clause could not be found")
    if result["evidence_support"] == "unsupported" and status in {"covered", "partial", "duplicate"}:
        result["escalation"].append("statement about cover without supporting clause")
    if sev == "high" and result["evidence_support"] in {"weak", "unsupported"}:
        result["escalation"].append("high impact finding with weak evidence")
    if calculation and calculation.get("status") == "unresolved" and sev == "high":
        result["escalation"].append("calculation has unresolved conditions")
    if result["rule_consistency"] == "mismatch":
        result["escalation"].append("a figure in the statement does not trace to a record")

    if not result["source_exists"] or result["independent_check"] == "rejected":
        overall = "rejected"
    elif result["escalation"] or result["independent_check"] == "unresolved":
        overall = "requires_review"
    elif result["contradictions"] or result["evidence_support"] != "supported" or result["independent_check"] == "revise":
        overall = "qualified"
    else:
        overall = "verified"
    if status == "gap" and overall == "qualified" and not result["contradictions"] and result["rule_consistency"] == "passed":
        overall = "verified"  # a confirmed absence across the scanned portfolio
    result["overall"] = overall
    return result
