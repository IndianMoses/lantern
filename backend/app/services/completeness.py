"""Rule based claim completeness assessment.

Completeness is administrative: are the fields and documents present that
the insurer's procedure asks for? It says nothing about whether the claim
is covered or will be approved, and the output states that explicitly.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

from ..models import Claim, Document, Policy

MANDATORY_FIELDS = ["incident_type", "incident_date", "location", "description"]


def assess(claim: Claim, policy: Policy | None, checklist: list[dict], documents: Iterable[Document]) -> dict:
    docs = list(documents)
    missing_fields: list[str] = []
    for f in MANDATORY_FIELDS:
        if not getattr(claim, f, None):
            missing_fields.append(f)
    if claim.estimated_loss is None and not claim.loss_unknown:
        missing_fields.append("estimated_loss_or_unknown")

    required_docs = [i for i in checklist if i["kind"] == "document" and i["status"] == "required"]
    recommended_docs = [i for i in checklist if i["kind"] == "document" and i["status"] == "recommended"]
    missing_required = [i for i in required_docs if not i["satisfied"]]
    missing_recommended = [i for i in recommended_docs if not i["satisfied"]]
    required_actions = [i for i in checklist if i["kind"] == "action" and i["status"] == "required" and not i["satisfied"] and i["key"] != "notify_in_time"]
    required_info = [i for i in checklist if i["kind"] == "information" and i["status"] == "required" and not i["satisfied"]]

    conflicts: list[dict] = []
    if policy and claim.incident_date:
        if claim.incident_date < policy.start_date or claim.incident_date > policy.end_date:
            conflicts.append({"code": "outside_policy_period", "en": f"The incident date {claim.incident_date.isoformat()} is outside the policy period {policy.start_date.isoformat()} to {policy.end_date.isoformat()}. The insurer will need an explanation, or a different policy may apply.", "zh": f"事件日期 {claim.incident_date.isoformat()} 不在保险期间 {policy.start_date.isoformat()} 至 {policy.end_date.isoformat()} 之内，需要向保险公司说明，或可能适用其他保单。"})
    if claim.incident_date and claim.incident_date > date.today():
        conflicts.append({"code": "future_date", "en": "The incident date is in the future. Please check it.", "zh": "事件日期在未来，请核对。"})
    if claim.estimated_loss is not None and claim.items_involved:
        total_items = sum(float(i.get("value") or 0) for i in claim.items_involved)
        if total_items and abs(total_items - float(claim.estimated_loss)) > max(50.0, 0.25 * total_items):
            conflicts.append({"code": "loss_mismatch", "en": f"The estimated loss ({claim.estimated_loss:,.0f}) differs noticeably from the total of the listed items ({total_items:,.0f}). The insurer may ask which figure is right.", "zh": f"预计损失（{claim.estimated_loss:,.0f}）与所列物品合计（{total_items:,.0f}）差异明显，保险公司可能会询问以哪个为准。"})
    hashes: dict[str, list[str]] = {}
    for d in docs:
        hashes.setdefault(d.sha256, []).append(d.filename)
    for sha, names in hashes.items():
        if len(names) > 1:
            conflicts.append({"code": "duplicate_document", "en": f"The same file appears to have been uploaded more than once: {', '.join(names)}.", "zh": f"同一文件似乎被重复上传：{', '.join(names)}。"})
    unreadable = [d.filename for d in docs if d.extraction_status == "failed"]
    if unreadable:
        conflicts.append({"code": "unreadable_document", "en": f"Text could not be read from: {', '.join(unreadable)}. The insurer can still view the file, but check it opens correctly.", "zh": f"无法读取以下文件的文字：{', '.join(unreadable)}。保险公司仍可查看文件，请确认能正常打开。"})
    if claim.safety_concern:
        conflicts.append({"code": "safety_first", "en": "You indicated someone may be hurt or in danger. Please contact the emergency services first; the claim can wait.", "zh": "你表示可能有人受伤或处于危险中，请先联系急救或报警，理赔可以稍后处理。"})

    total_checks = len(MANDATORY_FIELDS) + 1 + len(required_docs) + len(required_actions) + len(required_info) + len(recommended_docs)
    done = (len(MANDATORY_FIELDS) + 1 - len(missing_fields)) + (len(required_docs) - len(missing_required)) + (len(recommended_docs) - len(missing_recommended))
    progress = int(round(100 * done / total_checks)) if total_checks else 0
    blocking = bool(missing_fields or missing_required or required_actions or required_info or any(c["code"] in {"future_date", "safety_first"} for c in conflicts))

    return {
        "progress": progress,
        "ready": not blocking,
        "missing_fields": missing_fields,
        "missing_required_documents": [i["key"] for i in missing_required],
        "missing_recommended_documents": [i["key"] for i in missing_recommended],
        "outstanding_actions": [i["key"] for i in required_actions],
        "outstanding_information": [i["key"] for i in required_info],
        "conflicts": conflicts,
        "needs_human_review": [c["code"] for c in conflicts if c["code"] in {"outside_policy_period", "loss_mismatch"}],
        "document_count": len(docs),
        "note": {
            "en": "Completeness means the pack contains what the insurer's procedure asks for. It is not a view on whether the claim is covered or will be approved.",
            "zh": "“完整”指材料包包含保险公司程序要求的内容，并不代表对是否在保障范围内或能否获赔的判断。",
        },
    }
