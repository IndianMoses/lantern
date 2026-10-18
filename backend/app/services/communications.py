"""Template based drafting of letters and messages to insurers.

Templates only insert facts that exist on the claim, policy or decision
records. Anything unknown is left as a visible placeholder in square
brackets for the customer to complete, so the draft never invents dates,
reference numbers or promises. When the Claude API is configured the model
can refine the tone, but it is given the same facts and the same rule.
"""
from __future__ import annotations

from ..models import Claim, ClaimDecision, Organization, Policy, User

KINDS = {
    "claim_notification": {"en": "Initial claim notification", "zh": "首次理赔通知"},
    "progress_update": {"en": "Request for a progress update", "zh": "询问理赔进度"},
    "missing_evidence": {"en": "Submission of further evidence", "zh": "补充材料说明"},
    "clarification_request": {"en": "Request for clarification of a decision", "zh": "请求说明理赔结论"},
    "reconsideration_request": {"en": "Request for reconsideration or review", "zh": "申请复核"},
    "complaint_delay": {"en": "Complaint about delay", "zh": "投诉处理延误"},
    "information_response": {"en": "Response to an insurer's request", "zh": "回复保险公司要求"},
    "coverage_question": {"en": "Question about cover", "zh": "咨询保障范围"},
}


def _recipient(org: Organization | None) -> str | None:
    return org.claims_email if org else None


def draft(kind: str, *, user: User, claim: Claim | None, policy: Policy | None, org: Organization | None, decision: ClaimDecision | None = None, points: list[str] | None = None, documents: list[str] | None = None, lang: str = "en") -> dict:
    points = [p for p in (points or []) if p]
    docs = documents or []
    ref = claim.reference if claim else "[claim reference]"
    ins_ref = claim.insurer_reference if (claim and claim.insurer_reference) else None
    pol_no = policy.policy_number if policy else "[policy number]"
    org_name = org.name if org else ("the insurer" if lang != "zh" else "保险公司")
    inc_date = claim.incident_date.isoformat() if (claim and claim.incident_date) else "[date of incident]"
    inc_type = claim.incident_type.replace("_", " ") if claim else "[incident]"
    sub_date = claim.submitted_at.date().isoformat() if (claim and claim.submitted_at) else "[submission date]"
    name = user.full_name
    ref_line = (f"Claim reference: {ref}" + (f" (your reference {ins_ref})" if ins_ref else "")) if lang != "zh" else (f"理赔编号：{ref}" + (f"（贵司受理编号 {ins_ref}）" if ins_ref else ""))
    pts = ("\n".join(f"- {p}" for p in points) + "\n") if points else ""
    att = ("\n".join(f"- {d}" for d in docs) + "\n") if docs else ""

    if lang == "zh":
        if kind == "claim_notification":
            subject = f"理赔通知 — 保单 {pol_no} — {inc_date} {inc_type}"
            body = f"{org_name}理赔部：\n\n我是保单 {pol_no} 的被保险人{name}，现就 {inc_date} 发生的事件提出理赔申请。\n\n事件经过：{(claim.description if claim else '[经过]')}\n{('预计损失：' + format(claim.estimated_loss, ',.0f') + ' ' + claim.currency) if (claim and claim.estimated_loss is not None) else '预计损失：尚不确定'}\n{('报案编号：' + claim.authority_reference) if (claim and claim.authority_reference) else ''}\n\n随附材料：\n{att or '- [见附件]'}\n请确认受理并告知后续所需材料及理赔编号。\n\n{name}"
        elif kind == "progress_update":
            subject = f"理赔进度询问 — {ref}"
            body = f"{org_name}理赔部：\n\n{ref_line}\n保单号：{pol_no}\n\n上述理赔已于 {sub_date} 提交。请告知目前的处理进度、预计完成时间，以及是否还需要我提供其他材料。\n{pts}\n谢谢。\n\n{name}"
        elif kind == "missing_evidence":
            subject = f"补充材料 — {ref}"
            body = f"{org_name}理赔部：\n\n{ref_line}\n\n根据贵司的要求，现补充以下材料：\n{att or '- [材料名称]'}\n{pts}请确认收到，如仍有缺漏请告知。\n\n{name}"
        elif kind == "clarification_request":
            reason = decision.reason if decision else "[结论原文]"
            subject = f"请求说明理赔结论 — {ref}"
            body = f"{org_name}理赔部：\n\n{ref_line}\n\n我已收到贵司对该理赔的结论：“{reason}”\n\n为便于我理解该结论，请说明：\n- 结论所依据的具体条款及其原文；\n- 贵司认定事实所依据的材料；\n- 如我有补充材料，复核的途径和时限。\n{pts}\n谢谢。\n\n{name}"
        elif kind == "reconsideration_request":
            reason = decision.reason if decision else "[结论原文]"
            subject = f"申请复核理赔结论 — {ref}"
            body = f"{org_name}理赔部：\n\n{ref_line}\n\n贵司于 {(decision.created_at.date().isoformat() if decision else '[日期]')} 作出结论：“{reason}”\n\n我申请对该结论进行复核，理由如下：\n{pts or '- [请在此写明你认为结论有误的理由，并以事实为据]'}\n随附材料：\n{att or '- [补充材料]'}\n请在规定时限内书面回复复核结果。如贵司维持原结论，请告知进一步投诉的途径。\n\n{name}"
        elif kind == "complaint_delay":
            subject = f"投诉：理赔处理延误 — {ref}"
            body = f"{org_name}客户服务部：\n\n{ref_line}\n\n该理赔于 {sub_date} 提交，至今未收到结论或明确的处理时间表。请将本函作为正式投诉处理，并在贵司投诉处理时限内书面答复。\n{pts}\n{name}"
        elif kind == "information_response":
            subject = f"回复贵司的材料要求 — {ref}"
            body = f"{org_name}理赔部：\n\n{ref_line}\n\n就贵司的材料要求，回复如下：\n{pts or '- [回复内容]'}\n随附：\n{att or '- [材料]'}\n{name}"
        else:
            subject = f"关于保单 {pol_no} 的咨询"
            body = f"{org_name}客服部：\n\n我是保单 {pol_no} 的被保险人{name}，希望确认以下问题：\n{pts or '- [问题]'}\n请以书面形式答复，谢谢。\n\n{name}"
        return {"kind": kind, "subject": subject, "body": body, "recipient": _recipient(org), "language": "zh"}

    if kind == "claim_notification":
        subject = f"Claim notification — policy {pol_no} — {inc_type} on {inc_date}"
        body = (
            f"Dear {org_name} claims team,\n\nI am the policyholder on policy {pol_no} and I am writing to notify you of a {inc_type} incident on {inc_date}.\n\n"
            f"What happened: {(claim.description if claim else '[description]')}\n"
            + (f"Estimated loss: {claim.estimated_loss:,.0f} {claim.currency}\n" if (claim and claim.estimated_loss is not None) else "Estimated loss: not yet known\n")
            + (f"Reference from the authorities: {claim.authority_reference}\n" if (claim and claim.authority_reference) else "")
            + f"\nEnclosed:\n{att or '- [see attached documents]'}\nPlease confirm receipt, let me know your claim reference and tell me if you need anything further.\n\nKind regards,\n{name}"
        )
    elif kind == "progress_update":
        subject = f"Progress update request — {ref}"
        body = f"Dear {org_name} claims team,\n\n{ref_line}\nPolicy number: {pol_no}\n\nI submitted this claim on {sub_date}. Could you let me know its current status, the expected timescale for a decision, and whether you need any further information from me?\n{pts}\nThank you.\n\nKind regards,\n{name}"
    elif kind == "missing_evidence":
        subject = f"Further evidence — {ref}"
        body = f"Dear {org_name} claims team,\n\n{ref_line}\n\nFollowing your request, please find enclosed:\n{att or '- [document]'}\n{pts}Please confirm receipt and let me know if anything is still outstanding.\n\nKind regards,\n{name}"
    elif kind == "clarification_request":
        reason = decision.reason if decision else "[decision wording]"
        subject = f"Request for clarification of your decision — {ref}"
        body = f"Dear {org_name} claims team,\n\n{ref_line}\n\nThank you for your decision on this claim, which states: \"{reason}\"\n\nSo that I can understand the decision, please could you confirm:\n- the specific policy clauses relied on and their wording;\n- the evidence on which the findings of fact are based;\n- how and by when I may ask for the decision to be reviewed if I have further information.\n{pts}\nKind regards,\n{name}"
    elif kind == "reconsideration_request":
        reason = decision.reason if decision else "[decision wording]"
        subject = f"Request for reconsideration — {ref}"
        body = f"Dear {org_name} claims team,\n\n{ref_line}\n\nOn {(decision.created_at.date().isoformat() if decision else '[date]')} you decided: \"{reason}\"\n\nI would like to ask you to review this decision for the following reasons:\n{pts or '- [set out, factually, why you believe the decision should be reconsidered]'}\nEnclosed:\n{att or '- [supporting documents]'}\nPlease confirm the outcome of your review in writing. If you maintain the decision, please let me know how I can escalate my complaint.\n\nKind regards,\n{name}"
    elif kind == "complaint_delay":
        subject = f"Complaint: delay in handling claim {ref}"
        body = f"Dear {org_name} customer relations team,\n\n{ref_line}\n\nI submitted this claim on {sub_date} and have not yet received a decision or a clear timetable. Please treat this message as a formal complaint and respond in writing within your complaints procedure timescales.\n{pts}\nKind regards,\n{name}"
    elif kind == "information_response":
        subject = f"Response to your request — {ref}"
        body = f"Dear {org_name} claims team,\n\n{ref_line}\n\nIn response to your request:\n{pts or '- [your response]'}\nEnclosed:\n{att or '- [documents]'}\nKind regards,\n{name}"
    else:
        subject = f"Question about policy {pol_no}"
        body = f"Dear {org_name} customer service team,\n\nI am the policyholder on {pol_no} and would be grateful if you could confirm in writing:\n{pts or '- [your question]'}\nKind regards,\n{name}"
    return {"kind": kind, "subject": subject, "body": body, "recipient": _recipient(org), "language": "en"}
