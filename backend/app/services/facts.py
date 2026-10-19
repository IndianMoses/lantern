"""Structured policy extraction (stage 2 of the coverage pipeline).

Turns clause rows into typed ``PolicyFact`` records: limits, sub limits,
excesses, exclusions, conditions, waiting periods, notification deadlines,
territorial limits, definitions and claims procedure facts. Every fact keeps
the clause it came from, a defensible excerpt, a normalised value with its
unit, the policy's effective period, a confidence and whether it was stated
in the text or inferred from structure.

The extractor is deterministic (rules and regular expressions). A model
backed extractor can run alongside it through the same interface; its facts
are stored with its ``extractor`` name and start as ``unverified``.

What this extractor refuses to do: it never turns an absent clause into a
zero and never assumes cover exists because nothing excludes it. Expected
items that a wording does not state are recorded as ``not_found`` facts so the
analysis can say "unknown" rather than guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import FactStatus, Policy, PolicyClause, PolicyFact

EXTRACTOR = "rules"
EXTRACTOR_VERSION = "1.2"

# subjects the engine understands (shared with the gap engine's tag vocabulary)
SUBJECT_TAGS = [
    "contents", "buildings", "bicycle", "valuables", "jewellery", "money", "business_equipment", "personal_possessions", "laptop", "phone", "gadget",
    "ebike", "battery", "vehicle", "third_party", "liability", "medical", "cancellation", "baggage", "delay", "vet_fees", "pet", "tenants_liability",
    "own_damage", "windscreen", "theft", "fire", "escape_of_water", "storm", "flood", "accidental_damage", "personal_accident",
]

DURATION_PATTERNS = [
    # English
    (re.compile(r"within\s+(\d+)\s+(hours?|days?|working days?|weeks?|months?)", re.I), "en"),
    (re.compile(r"(\d+)\s*(hours?|days?)\s+of\s+(?:the\s+)?(?:start|discover|discovery|return|damage|incident|loss)", re.I), "en"),
    (re.compile(r"(?:more than|exceed(?:s|ing)?|up to|not exceed(?:ing)?)\s+(\d+)\s+(days?|hours?|consecutive days)", re.I), "en"),
    # Chinese
    (re.compile(r"(\d+)\s*(小时|日|天|个工作日|工作日)内"), "zh"),
    (re.compile(r"满\s*(\d+)\s*(日|天)"), "zh"),
    (re.compile(r"(\d+)\s*(日|天)(?:以上|以内)"), "zh"),
]
MONEY_PATTERNS = [
    (re.compile(r"£\s?([\d,]+(?:\.\d+)?)"), "GBP"),
    (re.compile(r"(?:RMB|¥|￥)\s?([\d,]+(?:\.\d+)?)"), "CNY"),
    (re.compile(r"([\d,]+(?:\.\d+)?)\s*元"), "CNY"),
]
PERCENT = re.compile(r"(\d{1,3})\s*%")

# expected facts per product, used to record explicit "not found" items
EXPECTED = {
    "home": [("limit", "contents"), ("limit", "buildings"), ("excess", "general"), ("notification_deadline", "claims")],
    "renters": [("limit", "contents"), ("excess", "general"), ("notification_deadline", "claims")],
    "motor": [("coverage", "third_party"), ("excess", "general"), ("territorial_limit", "use")],
    "travel": [("limit", "medical"), ("territorial_limit", "area"), ("excess", "general"), ("notification_deadline", "claims")],
    "ebike": [("coverage", "third_party"), ("limit", "theft"), ("excess", "general"), ("notification_deadline", "claims")],
    "gadget": [("coverage", "accidental_damage"), ("excess", "general"), ("notification_deadline", "claims")],
    "pet": [("limit", "vet_fees"), ("waiting_period", "illness"), ("excess", "general"), ("notification_deadline", "claims")],
}


@dataclass
class FactDTO:
    fact_type: str
    subject: str
    tags: list[str]
    value: float | None
    unit: str | None
    text_excerpt: str
    clause_id: str | None
    status: str = FactStatus.KNOWN
    source: str = "stated"
    confidence: float = 1.0
    ambiguity: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"fact_type": self.fact_type, "subject": self.subject, "tags": self.tags, "value": self.value, "unit": self.unit, "excerpt": self.text_excerpt, "clause_id": self.clause_id, "status": self.status, "source": self.source, "confidence": self.confidence, "ambiguity": self.ambiguity}


def _excerpt(text: str, needle: str | None = None, width: int = 220) -> str:
    text = " ".join(text.split())
    if needle and needle in text:
        i = text.index(needle)
        start = max(0, i - width // 2)
        return ("…" if start else "") + text[start : start + width] + ("…" if start + width < len(text) else "")
    return text[:width] + ("…" if len(text) > width else "")


def _subject_for(clause: PolicyClause) -> str:
    tags = clause.tags or []
    for t in SUBJECT_TAGS:
        if t in tags:
            return t
    return (tags[0] if tags else clause.kind) or "general"


def _unit_days(unit: str) -> tuple[float, str]:
    u = unit.lower()
    if u.startswith("hour") or u == "小时":
        return 1.0, "hours"
    if u.startswith("week"):
        return 7.0, "days"
    if u.startswith("month"):
        return 30.0, "days"
    return 1.0, "days"


def extract_clause_facts(clause: PolicyClause, policy: Policy) -> list[FactDTO]:
    """Facts for one clause. Deterministic; the same clause always yields the same facts."""
    out: list[FactDTO] = []
    text = clause.text or ""
    tags = list(clause.tags or [])
    conf = float(clause.confidence or 1.0)
    subject = _subject_for(clause)
    cur = policy.currency or "GBP"

    base_type = {"coverage": "coverage", "exclusion": "exclusion", "condition": "condition", "limit": "limit", "definition": "definition", "claims_procedure": "claims_procedure", "general": "general"}.get(clause.kind, "general")
    if base_type == "limit" and "excess" in tags:
        base_type = "excess"
    # the clause itself is a fact (its presence and classification)
    out.append(FactDTO(base_type, subject, tags, clause.amount if base_type in {"limit", "excess", "coverage", "definition"} else None, cur if (clause.amount and base_type in {"limit", "excess", "coverage", "definition"}) else None, _excerpt(text), clause.id, confidence=conf, source="stated" if conf >= 0.9 else "inferred"))

    # amounts quoted inside the text become limit / sub limit facts
    seen_amounts: set[float] = set()
    for pat, ccy in MONEY_PATTERNS:
        for m in pat.finditer(text):
            try:
                v = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if v in seen_amounts:
                continue
            seen_amounts.add(v)
            window = text[max(0, m.start() - 90) : m.end() + 40].lower()
            if "excess" in window or "免赔" in window:
                ftype = "excess"
            elif "single item" in window or "any one item" in window or "pair or set" in window or "单件" in window:
                ftype = "sub_limit"
            elif base_type in {"exclusion", "definition", "condition", "claims_procedure"} and ("over" in window or "more than" in window or "above" in window or "超过" in window):
                ftype = "threshold"
            else:
                ftype = "limit" if base_type in {"coverage", "limit", "definition"} else "amount"
            out.append(FactDTO(ftype, subject, tags, v, ccy, _excerpt(text, m.group(0)), clause.id, confidence=conf))

    # durations: waiting periods, notification deadlines, trip limits, unoccupancy
    for pat, _lang in DURATION_PATTERNS:
        for m in pat.finditer(text):
            n = float(m.group(1))
            mult, unit = _unit_days(m.group(2))
            value = n * mult
            window = text[max(0, m.start() - 80) : m.end() + 60].lower()
            if "waiting" in window or "等待" in window or ("未追回" in window) or ("first shows" in window) or ("satisfied" in window and "未" in window):
                ftype = "waiting_period"
            elif any(k in window for k in ("police", "crime reference", "公安", "报警", "派出所", "carrier")) and any(k in window for k in ("report", "obtained", "报案", "回执")):
                ftype = "police_report_deadline"
            elif any(k in window for k in ("notify", "tell us", "report", "notified", "报案", "通知", "submitted", "claims must")):
                ftype = "notification_deadline"
            elif any(k in window for k in ("trip", "行程", "period of insurance", "any one period")):
                ftype = "trip_limit"
            elif "unoccupied" in window or "lived in" in window:
                ftype = "occupancy_limit"
            elif "recovered" in window or "追回" in window or "within 30 days" in window and "stolen" in window:
                ftype = "waiting_period"
            elif base_type == "exclusion":
                ftype = "exclusion_period"
            else:
                ftype = "time_limit"
            out.append(FactDTO(ftype, subject, tags, value, unit, _excerpt(text, m.group(0)), clause.id, confidence=conf))

    for m in PERCENT.finditer(text):
        out.append(FactDTO("percentage", subject, tags, float(m.group(1)), "percent", _excerpt(text, m.group(0)), clause.id, confidence=conf))

    if "geographic_scope" in tags or "territorial" in tags or "area" in (clause.title or "").lower():
        scope = {"excludes": [t for t in ("usa", "canada", "caribbean") if f"excl_{t}" in tags or "excl_usa_canada" in tags and t in ("usa", "canada")], "includes": [t for t in ("worldwide", "europe", "usa", "uk") if t in tags and "excl_usa_canada" not in tags]}
        out.append(FactDTO("territorial_limit", "area", tags, None, "flag", _excerpt(text), clause.id, confidence=conf, extra=scope))
    if "not_selected" in tags:
        out.append(FactDTO("extension_not_selected", subject, tags, None, "flag", _excerpt(text), clause.id, confidence=conf))
    if "waiting_period" in tags and not any(f.fact_type == "waiting_period" for f in out):
        out.append(FactDTO("waiting_period", subject, tags, clause.amount, "days" if clause.amount else None, _excerpt(text), clause.id, status=FactStatus.KNOWN if clause.amount else FactStatus.AMBIGUOUS, ambiguity=None if clause.amount else "The clause refers to a waiting period but no duration could be read from the text.", confidence=conf))
    return out


def extract_policy_facts(policy: Policy, clauses: list[PolicyClause]) -> list[FactDTO]:
    facts: list[FactDTO] = []
    for cl in clauses:
        facts.extend(extract_clause_facts(cl, policy))
    # conflicts: two stated values for the same (type, subject) inside one policy
    by_key: dict[tuple[str, str], list[FactDTO]] = {}
    for f in facts:
        if f.fact_type in {"excess", "waiting_period"} and f.value is not None and f.clause_id:
            by_key.setdefault((f.fact_type, f.subject), []).append(f)
    for (ftype, subject), group in by_key.items():
        clauses = {f.clause_id for f in group}
        values = {f.value for f in group}
        # two different clauses stating different values for the same subject is a real conflict;
        # several figures inside one clause are different cases of the same rule, not a conflict
        if len(values) > 1 and len(clauses) > 1:
            for f in group:
                f.status = FactStatus.CONFLICTING
                f.ambiguity = f"Different {ftype.replace('_', ' ')} values are stated for {subject} in different clauses: {sorted(values)}; which one applies must be confirmed with the insurer."
    # expected items that are absent are recorded as not found
    present = {(f.fact_type if f.fact_type != "sub_limit" else "limit", f.subject) for f in facts}
    present_types = {f.fact_type for f in facts}
    for ftype, subject in EXPECTED.get(policy.insurance_type, []):
        if subject == "general":
            found = ftype in present_types or (ftype == "excess" and policy.excess_amount is not None)
        elif subject == "claims":
            found = "notification_deadline" in present_types
        elif subject in {"use", "area"}:
            found = "territorial_limit" in present_types
        elif subject == "illness":
            found = "waiting_period" in present_types
        else:
            found = (ftype, subject) in present or any(f.fact_type == ftype and subject in f.tags for f in facts)
        if not found:
            facts.append(FactDTO(ftype, subject, [subject], None, None, "", None, status=FactStatus.NOT_FOUND, source="inferred", confidence=1.0, ambiguity=f"No {ftype.replace('_', ' ')} for {subject} was found in the wording. Treat it as unknown, not as zero."))
    # the schedule level excess is a stated fact of the policy record
    if policy.excess_amount is not None:
        facts.append(FactDTO("excess", "general", ["excess"], float(policy.excess_amount), policy.currency, "Excess shown on the policy schedule.", None, confidence=1.0))
    return facts


def refresh_policy_facts(db: Session, policy: Policy, clauses: list[PolicyClause] | None = None, *, extractor: str = EXTRACTOR) -> list[PolicyFact]:
    """Replace the stored facts of one policy with a fresh extraction."""
    clauses = clauses if clauses is not None else list(policy.clauses)
    dtos = extract_policy_facts(policy, clauses)
    db.execute(delete(PolicyFact).where(PolicyFact.policy_id == policy.id, PolicyFact.extractor == extractor))
    rows: list[PolicyFact] = []
    for d in dtos:
        row = PolicyFact(policy_id=policy.id, clause_id=d.clause_id, fact_type=d.fact_type, subject=d.subject, tags=d.tags, value=d.value, unit=d.unit, text_excerpt=d.text_excerpt, status=d.status, source=d.source, confidence=d.confidence, effective_from=policy.start_date, effective_to=policy.end_date, verification_status="verified" if (d.clause_id and d.confidence >= 0.9 and d.status == FactStatus.KNOWN) else "unverified", ambiguity=d.ambiguity, extractor=extractor, extractor_version=EXTRACTOR_VERSION)
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def facts_for_policies(db: Session, policy_ids: list[str]) -> dict[str, list[PolicyFact]]:
    if not policy_ids:
        return {}
    rows = db.execute(select(PolicyFact).where(PolicyFact.policy_id.in_(policy_ids))).scalars().all()
    out: dict[str, list[PolicyFact]] = {pid: [] for pid in policy_ids}
    for r in rows:
        out.setdefault(r.policy_id, []).append(r)
    return out


def fact_dict(f: PolicyFact, policy_number: str | None = None, clause_ref: str | None = None) -> dict:
    return {
        "id": f.id, "policy_id": f.policy_id, "policy_number": policy_number, "clause_id": f.clause_id, "clause_ref": clause_ref, "fact_type": f.fact_type, "subject": f.subject, "tags": f.tags,
        "value": f.value, "unit": f.unit, "excerpt": f.text_excerpt, "status": f.status, "source": f.source, "confidence": f.confidence,
        "effective_from": f.effective_from.isoformat() if f.effective_from else None, "effective_to": f.effective_to.isoformat() if f.effective_to else None,
        "verification_status": f.verification_status, "ambiguity": f.ambiguity, "extractor": f"{f.extractor}@{f.extractor_version}",
    }


def in_force_on(policy: Policy, when: date | None) -> bool:
    if when is None:
        return policy.status == "active"
    return policy.start_date <= when <= policy.end_date
