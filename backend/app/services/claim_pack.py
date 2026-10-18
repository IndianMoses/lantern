"""Claim pack: the structured payload sent to an insurer adapter and the PDF
the customer can download or forward through any channel (assisted mode)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..models import Claim, Communication, Document, Policy, User
from ..seed.files import make_pdf
from .taxonomy import incident_label, insurance_label


def build_pack(claim: Claim, policy: Policy | None, customer: User, documents: list[Document], checklist: list[dict], completeness: dict, coverage: dict, cover_letter: Communication | None) -> dict:
    lang = claim.language or "en"
    pack = {
        "claim_reference": claim.reference,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "language": lang,
        "policy_number": policy.policy_number if policy else None,
        "policy": {
            "provider": policy.provider_name if policy else None,
            "product": policy.product_name if policy else None,
            "insurance_type": claim.insurance_type,
            "period": {"start": policy.start_date.isoformat(), "end": policy.end_date.isoformat()} if policy else None,
            "excess": policy.excess_amount if policy else None,
        },
        "customer": {"name": customer.full_name, "email": customer.email, "customer_id": customer.id},
        "incident": {
            "type": claim.incident_type,
            "type_label": incident_label(claim.incident_type, lang),
            "date": claim.incident_date.isoformat() if claim.incident_date else None,
            "time": claim.incident_time,
            "location": claim.location,
            "description": claim.description,
            "items_involved": claim.items_involved,
            "estimated_loss": claim.estimated_loss,
            "loss_unknown": claim.loss_unknown,
            "currency": claim.currency,
            "third_party_involved": claim.third_party_involved,
            "authorities_involved": claim.authorities_involved,
            "authority_reference": claim.authority_reference,
            "actions_taken": claim.actions_taken,
        },
        "documents": [
            {"document_id": d.id, "filename": d.filename, "type": d.doc_type, "sha256": d.sha256, "size_bytes": d.size_bytes, "checklist_item": d.checklist_item_key}
            for d in documents
        ],
        "checklist": [{"key": i["key"], "status": i["status"], "satisfied": i["satisfied"], "citations": [c["citation"] for c in i.get("citations", [])]} for i in checklist],
        "completeness": {"progress": completeness.get("progress"), "ready": completeness.get("ready"), "conflicts": [c["code"] for c in completeness.get("conflicts", [])]},
        "coverage_reading": {"verdict": coverage.get("verdict"), "covering_clauses": [c["citation"] for c in coverage.get("covering_clauses", [])], "possible_exclusions": [c["citation"] for c in coverage.get("possible_exclusions", [])], "note": "Customer side reading of the wording; not an admission or a coverage determination."},
        "cover_letter": {"subject": cover_letter.subject, "body": cover_letter.body} if cover_letter else None,
        "declaration": {
            "en": "The customer confirms that the information in this pack is true to the best of their knowledge and approves its submission to the insurer.",
            "zh": "客户确认本材料包中的信息据其所知真实无误，并同意提交给保险公司。",
        },
    }
    return pack


def render_pack_pdf(pack: dict, claim: Claim) -> Path:
    lang = pack.get("language", "en")
    out_dir = settings.upload_dir / "packs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{claim.reference}-claim-pack.pdf"
    inc = pack["incident"]
    pol = pack["policy"]
    if lang == "zh":
        paras = [
            "# 保单信息",
            f"保险公司：{pol['provider']}    产品：{pol['product']}    保单号：{pack['policy_number']}",
            f"保险期间：{pol['period']['start']} 至 {pol['period']['end']}" if pol.get("period") else "保险期间：未知",
            "# 事件信息",
            f"事件类型：{inc['type_label']}    日期：{inc['date'] or '未知'} {inc['time'] or ''}    地点：{inc['location'] or '未填写'}",
            f"经过：{inc['description']}",
            "涉及物品：" + ("；".join(f"{i.get('label')}（{i.get('value', '')}）" for i in inc["items_involved"]) if inc["items_involved"] else "未列明"),
            f"预计损失：{'不确定' if inc['loss_unknown'] else inc['estimated_loss']} {inc['currency']}",
            f"是否报案：{'是' if inc['authorities_involved'] else '否/不适用'}    编号：{inc['authority_reference'] or '无'}",
            f"已采取措施：{inc['actions_taken'] or '无'}",
            "# 随附文件",
        ] + [f"{d['filename']}（{d['type']}，SHA-256 {d['sha256'][:12]}…）" for d in pack["documents"]] + [
            "# 清单状态",
        ] + [f"{i['key']}：{i['status']}，{'已提供' if i['satisfied'] else '未提供'}" + (f"（依据 {', '.join(i['citations'])}）" if i["citations"] else "") for i in pack["checklist"]] + [
            "# 客户声明",
            pack["declaration"]["zh"],
        ]
        title = f"理赔材料包 — {pack['claim_reference']}"
    else:
        paras = [
            "# Policy",
            f"Provider: {pol['provider']}    Product: {pol['product']}    Policy number: {pack['policy_number']}",
            f"Period: {pol['period']['start']} to {pol['period']['end']}" if pol.get("period") else "Period: unknown",
            "# Incident",
            f"Type: {inc['type_label']}    Date: {inc['date'] or 'unknown'} {inc['time'] or ''}    Location: {inc['location'] or 'not given'}",
            f"Description: {inc['description']}",
            "Items involved: " + ("; ".join(f"{i.get('label')} ({i.get('value', '')})" for i in inc["items_involved"]) if inc["items_involved"] else "not itemised"),
            f"Estimated loss: {'unknown' if inc['loss_unknown'] else inc['estimated_loss']} {inc['currency']}",
            f"Reported to authorities: {'yes' if inc['authorities_involved'] else 'no or not applicable'}    Reference: {inc['authority_reference'] or 'none'}",
            f"Actions taken: {inc['actions_taken'] or 'none recorded'}",
            "# Enclosed documents",
        ] + [f"{d['filename']} ({d['type']}, SHA-256 {d['sha256'][:12]}…)" for d in pack["documents"]] + [
            "# Checklist status",
        ] + [f"{i['key']}: {i['status']}, {'provided' if i['satisfied'] else 'not provided'}" + (f" (per {', '.join(i['citations'])})" if i["citations"] else "") for i in pack["checklist"]] + [
            "# Customer declaration",
            pack["declaration"]["en"],
        ]
        title = f"Claim pack — {pack['claim_reference']}"
    if pack.get("cover_letter"):
        paras += ["# Cover letter" if lang != "zh" else "# 致保险公司函", pack["cover_letter"]["subject"], pack["cover_letter"]["body"]]
    make_pdf(path, title, paras, header=f"{insurance_label(claim.insurance_type, lang)} · Lantern", watermark="Generated by Lantern from customer supplied information. Demonstration environment.")
    return path
