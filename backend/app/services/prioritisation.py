"""Transparent prioritisation of coverage findings.

Two separate dimensions, never merged into one score the customer cannot
unpick:

* risk priority: how consequential the exposure may be
  (critical_to_investigate, high, medium, low, information_needed)
* evidence confidence: how well the finding is substantiated (high, medium, low)

The weights are plain numbers in ``WEIGHTS`` and every result lists the
reasons that produced it. A low confidence finding is never presented as a
verified high priority gap: it is capped and marked for investigation.
"""
from __future__ import annotations

from datetime import date

WEIGHTS = {
    "severity": {"high": 3.0, "medium": 2.0, "low": 1.0},
    "status": {"gap": 1.0, "partial": 0.5, "duplicate": 0.5, "note": -2.0, "covered": -4.0, "unresolved": 0.5},
    "uncovered_over_1000": 1.0,
    "stated_concern": 1.0,
    "time_sensitive": 1.0,
    "legal_requirement": 1.5,
}
THRESHOLDS = {"critical_to_investigate": 5.0, "high": 3.5, "medium": 2.0}
LEGAL_NEEDS = {"motor_liability", "ebike_liability"}


def _concern_matches(need: str, concerns: list[str]) -> bool:
    text = " ".join(concerns).lower()
    keys = {
        "bicycle": ("bike", "自行车", "单车"), "travel_duplicate": ("twice", "paying", "重复"), "travel_usa": ("usa", "america", "美国"), "phone": ("phone", "手机"),
        "battery_fire": ("charging", "fire", "充电", "起火"), "laptop_away": ("laptop", "笔记本"), "pet_vet_fees": ("vet", "pet", "宠物"), "valuables": ("ring", "jewellery", "首饰"),
    }
    return any(k in text for k in keys.get(need, ()))


def prioritise(finding: dict, *, verification: dict, concerns: list[str] | None = None, policy_end_dates: dict[str, date] | None = None, today: date | None = None, waiting_period_active: bool = False) -> dict:
    today = today or date.today()
    reasons: list[dict] = []
    score = 0.0
    sev = finding.get("severity", "low")
    score += WEIGHTS["severity"].get(sev, 1.0)
    reasons.append({"factor": "severity", "value": sev, "weight": WEIGHTS["severity"].get(sev, 1.0)})
    status = finding.get("status", "note")
    score += WEIGHTS["status"].get(status, 0.0)
    reasons.append({"factor": "status", "value": status, "weight": WEIGHTS["status"].get(status, 0.0)})
    calc = finding.get("calculation") or {}
    if calc.get("uncovered") and calc["uncovered"] >= 1000:
        score += WEIGHTS["uncovered_over_1000"]
        reasons.append({"factor": "uncovered_portion", "value": calc["uncovered"], "weight": WEIGHTS["uncovered_over_1000"]})
    if concerns and _concern_matches(finding.get("need", ""), concerns):
        score += WEIGHTS["stated_concern"]
        reasons.append({"factor": "stated_concern", "value": True, "weight": WEIGHTS["stated_concern"]})
    time_sensitive = waiting_period_active
    for pn in finding.get("policies", []):
        end = (policy_end_dates or {}).get(pn)
        if end and 0 <= (end - today).days <= 30:
            time_sensitive = True
    if time_sensitive and status != "covered":
        score += WEIGHTS["time_sensitive"]
        reasons.append({"factor": "time_sensitive", "value": True, "weight": WEIGHTS["time_sensitive"]})
    if finding.get("need") in LEGAL_NEEDS and status == "gap":
        score += WEIGHTS["legal_requirement"]
        reasons.append({"factor": "legal_requirement", "value": True, "weight": WEIGHTS["legal_requirement"]})

    overall = verification.get("overall", "qualified")
    support = verification.get("evidence_support", "unsupported")
    if overall == "verified" and support == "supported":
        confidence = "high"
    elif overall in {"verified", "qualified"}:
        confidence = "medium"
    else:
        confidence = "low"

    if status == "covered":
        priority = "low"
    elif overall == "requires_review" or finding.get("kind") == "unresolved":
        priority = "information_needed"
    elif score >= THRESHOLDS["critical_to_investigate"]:
        priority = "critical_to_investigate"
    elif score >= THRESHOLDS["high"]:
        priority = "high"
    elif score >= THRESHOLDS["medium"]:
        priority = "medium"
    else:
        priority = "low"
    capped = False
    if confidence == "low" and priority == "critical_to_investigate":
        priority = "high"
        capped = True
    if finding.get("missing_information") and confidence == "low" and status in {"gap", "partial"}:
        priority = "information_needed"
    return {"risk_priority": priority, "evidence_confidence": confidence, "score": round(score, 2), "rationale": reasons, "capped_by_confidence": capped, "weights_version": "1"}
