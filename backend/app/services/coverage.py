"""Coverage view: which clauses of a policy appear to bear on an incident.

This is deliberately conservative. It groups matching clauses into
"appears to cover", "possible exclusion", "conditions to meet" and
"financial terms", always with citations, and never produces a yes or no
coverage decision. The wording of the summary follows the product rule:
"appears to include this type of cover, subject to the terms and
exclusions".
"""
from __future__ import annotations

from typing import Iterable

from ..models import Claim, Policy, PolicyClause
from .retrieval import ClauseIndex, extract_tags, index_from_rows
from .taxonomy import incident_tags

PERIL_TAGS = {
    "theft", "robbery", "loss", "fire", "storm", "flood", "escape_of_water", "water_damage", "accidental_damage", "collision", "own_damage",
    "vandalism", "screen_damage", "liquid_damage", "liability", "third_party", "windscreen", "medical", "illness", "injury", "personal_accident",
    "cancellation", "curtailment", "baggage", "delay", "missed_departure", "vet_fees", "death", "straying", "keys", "money",
}


SUBJECT_TAGS = {
    "bicycle", "ebike", "phone", "laptop", "tablet", "gadget", "vehicle", "contents", "buildings", "jewellery", "valuables", "pet", "dog",
    "money", "keys", "baggage", "personal_possessions", "business_equipment", "windscreen", "home", "rider",
}
TYPE_SUBJECTS = {
    "home": {"contents", "buildings", "home", "bicycle", "jewellery", "valuables", "laptop", "money", "business_equipment", "personal_possessions"},
    "renters": {"contents", "home", "bicycle", "laptop", "money", "personal_possessions"},
    "motor": {"vehicle", "windscreen", "keys"},
    "travel": {"baggage", "personal_possessions", "money", "gadget", "phone", "laptop"},
    "ebike": {"ebike", "rider"},
    "gadget": {"phone", "gadget", "tablet", "laptop"},
    "pet": {"pet", "dog"},
}


def _tag_overlap(a: Iterable[str], b: Iterable[str]) -> list[str]:
    return sorted(set(a) & set(b))


def _cite(cl: PolicyClause, extra: dict | None = None) -> dict:
    d = {
        "clause_id": cl.id,
        "policy_number": cl.policy.policy_number,
        "clause_ref": cl.clause_ref,
        "citation": f"{cl.policy.policy_number} §{cl.clause_ref}",
        "title": cl.title,
        "kind": cl.kind,
        "amount": cl.amount,
        "language": cl.language,
        "excerpt": cl.text[:280] + ("…" if len(cl.text) > 280 else ""),
    }
    if extra:
        d.update(extra)
    return d


def coverage_view(policy: Policy, clauses: list[PolicyClause], *, incident_type: str, description: str, estimated_loss: float | None = None, context_tags: Iterable[str] = ()) -> dict:
    inc_tags = set(incident_tags(incident_type))
    desc_tags = extract_tags(description or "")
    all_tags = inc_tags | desc_tags | set(context_tags)
    peril_tags = all_tags & PERIL_TAGS or inc_tags
    subject_tags = desc_tags & SUBJECT_TAGS
    type_subjects = TYPE_SUBJECTS.get(policy.insurance_type, set())
    subject_in_scope = (not subject_tags) or bool(subject_tags & type_subjects)

    index: ClauseIndex = index_from_rows(clauses)
    query = f"{description or ''} {incident_type.replace('_', ' ')}"
    scores = {h.clause.clause_id: h.score for h in index.search(query, extra_tags=all_tags, top_k=50)}

    covers, exclusions, conditions, financial = [], [], [], []
    for cl in clauses:
        cl_tags = set(cl.tags or [])
        if cl.kind == "coverage":
            overlap = _tag_overlap(cl_tags, peril_tags)
            if overlap or scores.get(cl.id, 0) > 2.5:
                not_selected = "not_selected" in cl_tags
                subj = _tag_overlap(cl_tags, subject_tags)
                covers.append(_cite(cl, {"matched_tags": overlap, "subject_tags": subj, "score": round(scores.get(cl.id, 0), 2), "not_selected": not_selected}))
        elif cl.kind == "exclusion":
            overlap = _tag_overlap(cl_tags, all_tags - {"exclusion"})
            if overlap:
                exclusions.append(_cite(cl, {"matched_tags": overlap, "score": round(scores.get(cl.id, 0), 2)}))
        elif cl.kind in {"condition", "claims_procedure"}:
            overlap = _tag_overlap(cl_tags, all_tags | {"time_limit", "claims_procedure"})
            if overlap and (cl.kind == "claims_procedure" or (cl_tags & all_tags)):
                conditions.append(_cite(cl, {"matched_tags": overlap}))
        elif cl.kind == "limit":
            financial.append(_cite(cl, {"matched_tags": _tag_overlap(cl_tags, all_tags)}))
    covers.sort(key=lambda c: (c["not_selected"], -(len(c["matched_tags"]) + 2 * len(c["subject_tags"])), -c["score"]))
    exclusions.sort(key=lambda c: (-len(c["matched_tags"]), -c["score"]))

    active_covers = [c for c in covers if not c["not_selected"]]
    subject_matched = any(c["subject_tags"] for c in active_covers) if subject_tags else True
    if active_covers and not subject_in_scope and not subject_matched:
        verdict = "unclear_subject"
    elif active_covers and exclusions:
        verdict = "appears_covered_with_possible_exclusion"
    elif active_covers:
        verdict = "appears_covered"
    elif covers:
        verdict = "possible_exclusion"  # only a not selected extension matched
    elif exclusions:
        verdict = "possible_exclusion"
    else:
        verdict = "not_addressed"

    limit_notes = []
    if estimated_loss is not None:
        for c in active_covers:
            if c["amount"] and c["amount"] > 50 and estimated_loss > c["amount"]:
                limit_notes.append({"citation": c["citation"], "limit": c["amount"], "estimated_loss": estimated_loss, "en": f"The estimated loss ({estimated_loss:,.0f}) is above the limit stated in {c['citation']} ({c['amount']:,.0f}); any payment would appear to be capped at the limit.", "zh": f"预计损失（{estimated_loss:,.0f}）超过 {c['citation']} 所载限额（{c['amount']:,.0f}），赔付可能以限额为上限。"})
    excess = policy.excess_amount
    excess_clause = next((f for f in financial if "excess" in (f.get("matched_tags") or []) or "Excess" in f["title"] or "免赔" in f["title"]), None)

    summary = {
        "en": _summary_en(verdict, policy, active_covers, exclusions, excess, excess_clause),
        "zh": _summary_zh(verdict, policy, active_covers, exclusions, excess, excess_clause),
    }
    return {
        "policy_id": policy.id,
        "policy_number": policy.policy_number,
        "policy_name": f"{policy.provider_name} {policy.product_name}",
        "incident_type": incident_type,
        "tags_considered": sorted(all_tags),
        "verdict": verdict,
        "subject_tags": sorted(subject_tags),
        "subject_matched": subject_matched,
        "covering_clauses": covers[:5],
        "possible_exclusions": exclusions[:5],
        "conditions": conditions[:6],
        "financial_terms": financial[:4],
        "limit_notes": limit_notes,
        "excess": excess,
        "summary": summary,
        "disclaimer": {
            "en": "This is a reading of the policy wording, not a coverage decision. The insurer decides claims; ambiguous clauses should be clarified with them.",
            "zh": "以上是对保单条款的解读，不是保险公司的理赔结论。是否赔付由保险公司决定；条款不明确处应向保险公司确认。",
        },
    }


def _summary_en(verdict, policy, covers, exclusions, excess, excess_clause) -> str:
    name = f"{policy.provider_name} {policy.product_name} ({policy.policy_number})"
    if verdict == "not_addressed":
        return f"I could not find a clause in {name} that addresses this type of incident. That does not mean it is excluded, only that the wording I have does not deal with it; the insurer should confirm."
    parts = []
    if verdict == "unclear_subject":
        cites = ", ".join(f"[{c['citation']}]" for c in covers[:2])
        parts.append(f"{name} covers this kind of event in general ({cites}), but I found no clause about the item you described, so it may be the wrong policy for it.")
    elif covers:
        cites = ", ".join(f"[{c['citation']}]" for c in covers[:3])
        parts.append(f"{name} appears to include this type of cover under {cites}, subject to the policy's terms, limits and exclusions.")
    else:
        parts.append(f"The only matching cover in {name} is an optional extension your schedule shows as not selected.")
    if exclusions:
        parts.append("An exclusion may be relevant: " + "; ".join(f"[{e['citation']}] ({e['title']})" for e in exclusions[:3]) + ". Whether it applies depends on the facts, which the insurer will assess.")
    if excess:
        ref = f" ([{excess_clause['citation']}])" if excess_clause else ""
        parts.append(f"An excess of {excess:,.0f} {policy.currency} would normally be deducted{ref}.")
    return " ".join(parts)


def _summary_zh(verdict, policy, covers, exclusions, excess, excess_clause) -> str:
    name = f"{policy.provider_name}{policy.product_name}（{policy.policy_number}）"
    if verdict == "not_addressed":
        return f"在{name}中没有找到涉及此类事件的条款。这不代表一定不赔，只说明现有条款没有规定，需向保险公司确认。"
    parts = []
    if verdict == "unclear_subject":
        cites = "、".join(f"[{c['citation']}]" for c in covers[:2])
        parts.append(f"{name}总体上承保此类事件（{cites}），但没有找到涉及你所述物品的条款，可能不是适用的保单。")
    elif covers:
        cites = "、".join(f"[{c['citation']}]" for c in covers[:3])
        parts.append(f"{name}在 {cites} 项下看起来包含此类保障，以条款、限额和除外责任为准。")
    else:
        parts.append(f"{name}中唯一相关的保障是附表显示未选择的可选扩展。")
    if exclusions:
        parts.append("可能相关的除外责任：" + "；".join(f"[{e['citation']}]（{e['title']}）" for e in exclusions[:3]) + "。是否适用取决于事实，由保险公司核定。")
    if excess:
        ref = f"（[{excess_clause['citation']}]）" if excess_clause else ""
        parts.append(f"通常会扣除 {excess:,.0f} {policy.currency} 的免赔额{ref}。")
    return "".join(parts)


def rank_policies_for_incident(policies: list[tuple[Policy, list[PolicyClause]]], *, incident_type: str, description: str) -> list[dict]:
    """Which of the customer's policies could respond to this incident."""
    ranked = []
    for policy, clauses in policies:
        view = coverage_view(policy, clauses, incident_type=incident_type, description=description)
        score = {"appears_covered": 3, "appears_covered_with_possible_exclusion": 2.8, "unclear_subject": 1.2, "possible_exclusion": 1, "not_addressed": 0}[view["verdict"]]
        score += 0.1 * len(view["covering_clauses"])
        subject_hits = sum(1 for c in view["covering_clauses"] if c.get("subject_tags"))
        score += 1.5 * min(subject_hits, 2)
        if view["subject_tags"] and set(view["subject_tags"]) & TYPE_SUBJECTS.get(policy.insurance_type, set()):
            score += 1.0
        ranked.append({"policy_id": policy.id, "policy_number": policy.policy_number, "policy_name": view["policy_name"], "insurance_type": policy.insurance_type, "verdict": view["verdict"], "score": round(score, 2), "status": policy.status, "covering_clauses": view["covering_clauses"][:2], "possible_exclusions": view["possible_exclusions"][:2]})
    ranked.sort(key=lambda r: (-r["score"], r["policy_number"]))
    return ranked
