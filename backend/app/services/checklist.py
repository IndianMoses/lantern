"""Claim checklist generation.

A checklist is built from three layers:

1. Base items that every claim of this incident type usually needs.
2. Policy specific items read from the policy's own claims procedure and
   condition clauses. These carry a citation to the clause that requires
   them, so the customer can see *why* an item is needed.
3. Satisfaction, computed from the documents attached to the claim and the
   fields already filled in.

Statuses: required, recommended, optional, not_applicable, awaiting_insurer.
Kinds: document (needs an upload), information (a field), action (something
to do, such as reporting to the police).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..models import Claim, Document, PolicyClause
from .taxonomy import incident_tags


@dataclass
class Item:
    key: str
    kind: str  # document | information | action
    status: str
    label_en: str
    label_zh: str
    reason_en: str
    reason_zh: str
    how_en: str
    how_zh: str
    doc_types: tuple[str, ...] = ()
    field_name: str | None = None
    citations: list[dict] = field(default_factory=list)
    satisfied: bool = False
    satisfied_by: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "key": self.key, "kind": self.kind, "status": self.status,
            "label": {"en": self.label_en, "zh": self.label_zh},
            "reason": {"en": self.reason_en, "zh": self.reason_zh},
            "how": {"en": self.how_en, "zh": self.how_zh},
            "doc_types": list(self.doc_types), "field_name": self.field_name,
            "citations": self.citations, "satisfied": self.satisfied, "satisfied_by": self.satisfied_by,
        }


def _item(key, kind, status, label_en, label_zh, reason_en, reason_zh, how_en, how_zh, doc_types=(), field_name=None) -> Item:
    return Item(key, kind, status, label_en, label_zh, reason_en, reason_zh, how_en, how_zh, tuple(doc_types), field_name)


# --------------------------------------------------------------- base items ---

def _base_items(insurance_type: str, incident_type: str) -> list[Item]:
    items: list[Item] = [
        _item("incident_description", "information", "required", "Description of what happened", "事件经过描述",
              "Every insurer needs a clear account of the incident to assess the claim.", "保险公司需要清楚的事件经过来评估理赔。",
              "Write what happened, when, where and what was affected, in your own words.", "用自己的话写清发生的时间、地点、经过和受影响的物品。", field_name="description"),
        _item("incident_date", "information", "required", "Date of the incident", "事件发生日期",
              "The date determines whether the policy was in force and whether reporting deadlines were met.", "日期决定保单是否在保险期间内以及是否满足报案时限。",
              "Give the date, or your best estimate if you discovered the loss later.", "填写日期；如果是事后发现，请填写大致日期。", field_name="incident_date"),
        _item("photos", "document", "recommended", "Photographs or video of the damage or scene", "损失或现场的照片或视频",
              "Photographs let the insurer assess the damage without delay and support what you describe.", "照片便于保险公司及时评估损失并佐证描述。",
              "Take wide and close up photos before anything is repaired or thrown away.", "在维修或丢弃之前拍摄全景和细节照片。", doc_types=("photo",)),
    ]
    if incident_type in {"theft", "loss", "baggage", "accidental_damage", "screen_damage", "liquid_damage", "fire", "escape_of_water", "flood", "storm", "vandalism"}:
        items.append(_item("proof_of_ownership", "document", "recommended", "Proof of ownership or value for the items claimed", "物品所有权或价值证明",
                           "Receipts, invoices, bank statements or photographs showing you owned the items and what they were worth.", "收据、发票、银行记录或照片，用于证明物品归属及价值。",
                           "Search email for order confirmations; a bank statement line or a product photo can help when a receipt is missing.", "可在邮箱中查找订单记录；没有发票时银行流水或产品照片也有帮助。", doc_types=("receipt", "proof_of_ownership")))
    if incident_type in {"escape_of_water", "flood", "storm", "fire", "accidental_damage", "collision", "vandalism", "screen_damage", "liquid_damage"}:
        items.append(_item("repair_estimate", "document", "recommended", "Repair estimate or quotation", "维修报价单",
                           "An estimate shows the likely cost and lets the insurer agree repairs quickly.", "报价单说明可能的费用，便于保险公司快速确认维修方案。",
                           "Ask a tradesperson or repair centre for a written, itemised estimate.", "请维修方提供书面、分项的报价。", doc_types=("estimate",)))
        items.append(_item("invoices", "document", "optional", "Invoices for emergency or completed work", "应急或已完成维修的发票",
                           "If you had to pay for emergency work, invoices support reimbursement.", "如已支付应急维修费用，发票是报销依据。",
                           "Keep every invoice and receipt, including call out charges.", "保留所有发票和收据，包括上门费。", doc_types=("receipt", "invoice")))
    if incident_type in {"theft", "vandalism", "baggage"} or (incident_type == "collision" and insurance_type in {"motor", "ebike"}):
        items.append(_item("police_report", "document", "recommended", "Police or carrier report with a reference number", "报案回执或承运人报告（含编号）",
                           "A reference number shows the incident was reported to the authorities.", "报案编号证明事件已向有关部门报告。",
                           "Report online or at a station and keep the reference or receipt.", "线上或到派出所报案，并保存回执或编号。", doc_types=("police_report",)))
        items.append(_item("report_to_police", "action", "recommended", "Report the incident to the police", "向公安机关报案",
                           "Most policies require theft and vandalism to be reported promptly.", "大多数保单要求及时报案。",
                           "Do this as soon as possible and note the date and time you reported it.", "尽快报案并记录报案时间。", field_name="authorities_involved"))
    if incident_type in {"collision", "third_party_liability"}:
        items.append(_item("third_party_details", "information", "required", "Details of the other party", "对方信息",
                           "Insurers need the other party's name, contact and insurer to handle liability.", "保险公司需要对方姓名、联系方式及保险公司信息处理责任问题。",
                           "Exchange details at the scene where safe; photograph documents and registration plates.", "在安全情况下现场交换信息，拍下证件和车牌。", field_name="third_party_involved"))
        items.append(_item("correspondence", "document", "optional", "Letters, messages or claims received from the other party", "对方发来的信函、消息或索赔材料",
                           "Anything the other party sends should be passed to the insurer unanswered.", "对方发来的任何材料应原样转交保险公司。",
                           "Upload screenshots or scans; do not reply admitting fault.", "上传截图或扫描件；不要回复承认责任。", doc_types=("correspondence",)))
    if incident_type in {"medical", "illness", "injury", "rider_injury"}:
        items.append(_item("medical_documents", "document", "required", "Medical or veterinary records and itemised invoices", "医疗 / 兽医记录及分项发票",
                           "Treatment records and itemised bills are the basis of a medical claim.", "诊疗记录和分项账单是医疗类理赔的依据。",
                           "Ask the clinic for a written diagnosis, treatment summary and itemised invoice.", "向医院索取诊断证明、治疗摘要和分项发票。", doc_types=("medical", "receipt", "invoice")))
    if incident_type in {"cancellation", "delay"}:
        items.append(_item("booking_documents", "document", "required", "Booking confirmations, cancellation invoices and carrier confirmation", "预订确认、取消费用单据及承运人证明",
                           "They show what you paid, what was refunded and the cause of the disruption.", "证明已支付金额、已退金额及延误原因。",
                           "Download confirmations from the booking site and request written confirmation of the delay or cancellation from the carrier.", "从预订平台下载确认单，并向承运人索取延误或取消的书面证明。", doc_types=("booking", "correspondence", "receipt")))
    if insurance_type == "pet":
        items.append(_item("vet_claim_form", "document", "recommended", "Insurer claim form completed by the treating vet", "兽医填写的保险公司理赔表",
                           "Pet insurers usually require their own form signed by the vet.", "宠物保险公司通常要求兽医签署专用表格。",
                           "Download the form from the insurer and ask the practice to complete it.", "从保险公司下载表格并请诊所填写。", doc_types=("claim_form",)))
    return items


# ------------------------------------------------------- policy specific ---

_CLAUSE_TAG_TO_ITEM = {
    "police_report": ("police_report", "required"),
    "receipt_required": ("proof_of_ownership", "required"),
    "proof_of_ownership": ("proof_of_ownership", "required"),
    "estimates": ("repair_estimate", "required"),
    "repair_quote": ("repair_estimate", "required"),
    "invoice": ("invoices", "recommended"),
    "photos": ("photos", "required"),
    "medical_report": ("medical_documents", "required"),
    "vet_history": ("vet_history", "required"),
    "imei": ("device_identity", "required"),
    "third_party_details": ("third_party_details", "required"),
}

_EXTRA_ITEMS = {
    "vet_history": _item("vet_history", "document", "required", "Full clinical history from every vet practice attended", "全部就诊宠物医院的完整病历",
                         "The policy says the insurer will request the clinical history for the first claim; providing it up front avoids delay.", "保单约定首次理赔需提供完整病历，提前提供可避免延误。",
                         "Ask each practice to email the records to you.", "请各诊所将病历发送给你。", doc_types=("medical", "vet_history")),
    "device_identity": _item("device_identity", "document", "required", "Photo showing the device IMEI or boot screen", "显示 IMEI 或开机界面的设备照片",
                             "The insurer must match the damaged device to the one insured.", "保险公司需要确认受损设备就是承保设备。",
                             "Dial *#06# or photograph Settings > About, together with the damage.", "拨打 *#06# 或拍摄设置中的关于手机页面，并连同损坏部位一起拍摄。", doc_types=("photo", "device_identity")),
}


def _citation(cl: PolicyClause) -> dict:
    return {"clause_id": cl.id, "policy_number": cl.policy.policy_number, "clause_ref": cl.clause_ref, "citation": f"{cl.policy.policy_number} §{cl.clause_ref}", "title": cl.title}


def build_checklist(claim: Claim, clauses: Iterable[PolicyClause], documents: Iterable[Document]) -> list[dict]:
    items = {it.key: it for it in _base_items(claim.insurance_type, claim.incident_type)}
    tags = set(incident_tags(claim.incident_type))
    docs = list(documents)

    for cl in clauses:
        if cl.kind not in {"claims_procedure", "condition", "coverage"}:
            continue
        cl_tags = set(cl.tags or [])
        # only procedure and condition clauses, or coverage clauses that mention the incident
        if cl.kind == "coverage" and not (cl_tags & tags):
            continue
        for tag, (key, status) in _CLAUSE_TAG_TO_ITEM.items():
            if tag not in cl_tags:
                continue
            if key == "police_report" and not (tags & {"theft", "police_report", "vandalism", "collision", "robbery"}):
                continue
            if key in ("medical_documents",) and not (tags & {"medical", "injury", "illness", "personal_accident"}):
                continue
            if key == "third_party_details" and not (tags & {"third_party", "collision", "liability"}):
                continue
            if key == "device_identity" and claim.insurance_type != "gadget":
                continue
            if key == "vet_history" and claim.insurance_type != "pet":
                continue
            if key == "repair_estimate" and not (tags & {"accidental_damage", "collision", "own_damage", "escape_of_water", "water_damage", "storm", "fire", "flood", "screen_damage", "liquid_damage", "vandalism"}):
                continue
            it = items.get(key) or _EXTRA_ITEMS.get(key)
            if not it:
                continue
            it = items.setdefault(key, it)
            if _rank(status) > _rank(it.status):
                it.status = status
            if not any(c["clause_id"] == cl.id for c in it.citations):
                it.citations.append(_citation(cl))
        # reporting deadlines become an action item with the clause attached
        if "time_limit" in cl_tags and cl.kind == "claims_procedure":
            key = "notify_in_time"
            it = items.get(key)
            if not it:
                it = _item(key, "action", "required", "Notify the insurer within the policy's time limit", "在保单规定时限内通知保险公司",
                           "The claims conditions set a deadline for notification; late notice can delay or affect the claim.", "理赔条件规定了通知时限，逾期可能影响理赔。",
                           "Submitting this claim pack counts as notification; the clause gives the exact period.", "提交本理赔材料包即视为通知；具体时限见条款。")
                items[key] = it
            if not any(c["clause_id"] == cl.id for c in it.citations):
                it.citations.append(_citation(cl))

    # satisfaction
    for it in items.values():
        if it.kind == "document":
            matched = [d.id for d in docs if (d.checklist_item_key == it.key) or (not d.checklist_item_key and d.doc_type in it.doc_types)]
            it.satisfied = bool(matched)
            it.satisfied_by = matched
        elif it.kind == "information":
            value = getattr(claim, it.field_name, None) if it.field_name else None
            if it.key == "third_party_details":
                it.satisfied = value is not None
            else:
                it.satisfied = bool(value)
        elif it.kind == "action":
            if it.key == "report_to_police":
                it.satisfied = bool(claim.authorities_involved)
            elif it.key == "notify_in_time":
                it.satisfied = claim.status not in {"draft", "information_required", "ready_for_review", "awaiting_customer_approval", "submission_failed"}
    order = {"required": 0, "recommended": 1, "optional": 2, "awaiting_insurer": 3, "not_applicable": 4}
    return [it.to_dict() for it in sorted(items.values(), key=lambda i: (order.get(i.status, 9), i.kind != "information"))]


def _rank(status: str) -> int:
    return {"not_applicable": 0, "optional": 1, "awaiting_insurer": 1, "recommended": 2, "required": 3}.get(status, 0)
