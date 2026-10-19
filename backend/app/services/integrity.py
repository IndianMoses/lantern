"""Integrity checker ensemble for claims (defensive validation).

Seven independent checkers, each returning a typed result with its own
version, findings, evidence, severity, confidence and status, and a
deterministic aggregation layer with explicit escalation rules. The ensemble
flags things for a person to look at; it never decides a claim and never
accuses anyone. "No anomaly detected" is never presented as proof that a
document is authentic.

Statuses: passed · warning · failed_validation · inconclusive · human_review_required

Aggregation (in this order, nothing implicit):
  1. any failed_validation      → failed_validation   (blocks submission; the
                                   record itself is wrong: future date, document
                                   owned by someone else, policy not the customer's)
  2. any human_review_required  → human_review_required (does not block; routed
                                   to the handler with the evidence)
  3. any warning                → warning             (shown to the customer so
                                   they can fix or explain it)
  4. any inconclusive           → inconclusive        (a checker could not run or
                                   could not read the material)
  5. otherwise                  → passed
A warning is never converted into a failure, and no majority vote decides anything.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Claim, ClaimCheckResult, Document, Policy, PolicyClause, PolicyFact, User, utcnow
from .extraction import scan_for_injection

ENSEMBLE_VERSION = "1.0"
STATUS_ORDER = ["failed_validation", "human_review_required", "warning", "inconclusive", "passed"]


@dataclass
class Finding:
    code: str
    message: dict  # {"en":..., "zh":...}
    evidence: dict = field(default_factory=dict)
    severity: str = "low"  # low | medium | high
    audience: str = "both"  # customer | insurer | both

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "evidence": self.evidence, "severity": self.severity, "audience": self.audience}


@dataclass
class CheckerResult:
    checker_id: str
    version: str
    status: str = "passed"
    confidence: str = "medium"  # low | medium | high
    findings: list[Finding] = field(default_factory=list)
    note: dict = field(default_factory=dict)

    @property
    def requires_human_review(self) -> bool:
        return self.status == "human_review_required"

    def add(self, status: str, finding: Finding) -> None:
        self.findings.append(finding)
        if STATUS_ORDER.index(status) < STATUS_ORDER.index(self.status):
            self.status = status

    def to_dict(self) -> dict:
        return {"checker_id": self.checker_id, "version": self.version, "status": self.status, "confidence": self.confidence, "requires_human_review": self.requires_human_review, "findings": [f.to_dict() for f in self.findings], "note": self.note}


@dataclass
class Context:
    db: Session
    claim: Claim
    policy: Policy | None
    clauses: list[PolicyClause]
    facts: list[PolicyFact]
    documents: list[Document]
    owner: User | None
    today: date


# ----------------------------------------------------------------- helpers ---

DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    (re.compile(r"\b(\d{4})年(\d{1,2})月(\d{1,2})日"), "ymd"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "dmy_or_mdy"),
]
MONEY = re.compile(r"(?:£|¥|￥|RMB|GBP|CNY)\s?([\d,]+(?:\.\d{1,2})?)|([\d,]+(?:\.\d{1,2})?)\s*(?:元|GBP|CNY)", re.I)
TOTAL = re.compile(r"(?:total|合计|总计|金额)[^\d£¥￥]{0,12}(?:£|¥|￥)?\s?([\d,]+(?:\.\d{1,2})?)", re.I)
INVOICE_NO = re.compile(r"(?:invoice|inv\.?|receipt|发票|回执|受案编号|编号)\s*(?:no\.?|number|#|号码|代码|：|:)?\s*[:：#]?\s*([A-Z]{0,4}[-/]?[A-Z0-9（）()〔〕\-一-鿿]{3,30})", re.I)


def _dates_in(text: str) -> tuple[list[date], bool]:
    """Dates found in a text and whether any were ambiguous (dd/mm vs mm/dd)."""
    out: list[date] = []
    ambiguous = False
    for pat, kind in DATE_PATTERNS:
        for m in pat.finditer(text or ""):
            try:
                if kind == "ymd":
                    out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
                else:
                    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    if a <= 12 and b <= 12 and a != b:
                        ambiguous = True
                    if a > 12:
                        out.append(date(y, b, a))
                    elif b > 12:
                        out.append(date(y, a, b))
                    else:
                        out.append(date(y, b, a))  # day first (UK style) when both are plausible
            except ValueError:
                continue
    return out, ambiguous


def _amounts_in(text: str) -> list[float]:
    vals = []
    for m in MONEY.finditer(text or ""):
        raw = m.group(1) or m.group(2)
        try:
            vals.append(float(raw.replace(",", "")))
        except (ValueError, AttributeError):
            pass
    return vals


def _normalise_ref(s: str) -> str:
    return re.sub(r"[\s\-_/（）()〔〕\[\]【】:：#]", "", s or "").upper()


def _shingles(text: str, n: int = 5) -> set[str]:
    words = re.findall(r"[\w一-鿿]+", (text or "").lower())
    if len(words) < n:
        return set(words)
    return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _read_head(doc: Document, n: int = 4096) -> bytes:
    try:
        with open(doc.storage_path, "rb") as fh:
            return fh.read(n)
    except OSError:
        return b""


def _read_all(doc: Document, limit: int = 20 * 1024 * 1024) -> bytes:
    try:
        return Path(doc.storage_path).read_bytes()[:limit]
    except OSError:
        return b""


# ---------------------------------------------------------------- checkers ---


def check_dates(ctx: Context) -> CheckerResult:
    r = CheckerResult("dates", "1.0", confidence="high")
    c, p = ctx.claim, ctx.policy
    if c.incident_date is None:
        r.add("inconclusive", Finding("incident_date_missing", {"en": "No incident date has been recorded yet.", "zh": "尚未记录事件日期。"}, audience="customer"))
        return r
    if c.incident_date > ctx.today:
        r.add("failed_validation", Finding("incident_in_future", {"en": f"The incident date {c.incident_date.isoformat()} is in the future.", "zh": f"事件日期 {c.incident_date.isoformat()} 在未来。"}, {"incident_date": c.incident_date.isoformat(), "today": ctx.today.isoformat()}, severity="high"))
    if p:
        if not (p.start_date <= c.incident_date <= p.end_date):
            # legitimate explanations exist (renewal chain, wrong policy selected), so a person decides
            r.add("human_review_required", Finding("incident_outside_policy_period", {"en": f"The incident date {c.incident_date.isoformat()} is outside the policy period {p.start_date.isoformat()} to {p.end_date.isoformat()} of {p.policy_number}. Another version of the policy may apply.", "zh": f"事件日期 {c.incident_date.isoformat()} 不在保单 {p.policy_number} 的保险期间（{p.start_date.isoformat()} 至 {p.end_date.isoformat()}）内，可能适用另一版本的保单。"}, {"policy_period": [p.start_date.isoformat(), p.end_date.isoformat()], "policy_version": p.version}, severity="high"))
        # notification deadline stated in the wording
        deadlines = [f for f in ctx.facts if f.fact_type == "notification_deadline" and f.value and f.unit in {"days", "hours"}]
        if deadlines:
            f = max(deadlines, key=lambda x: x.value if x.unit == "days" else x.value / 24)
            limit_days = f.value if f.unit == "days" else f.value / 24
            reported = (c.created_at.date() if c.created_at else ctx.today)
            elapsed = (reported - c.incident_date).days
            if elapsed > limit_days:
                r.add("warning", Finding("reported_after_deadline", {"en": f"The claim was started {elapsed} days after the incident; the wording asks for notification within {int(limit_days)} days.", "zh": f"理赔在事件发生 {elapsed} 天后才开始，而条款要求在 {int(limit_days)} 天内报案。"}, {"elapsed_days": elapsed, "limit_days": limit_days, "clause_id": f.clause_id}, severity="medium"))
    # document dates versus the incident
    for d in ctx.documents:
        if not d.extracted_text:
            continue
        found, ambiguous = _dates_in(d.extracted_text)
        if ambiguous:
            r.add("inconclusive", Finding("ambiguous_date_format", {"en": f"{d.filename} contains dates whose day and month order is ambiguous; they were not used.", "zh": f"{d.filename} 中的日期格式存在日/月歧义，未予采用。"}, {"document_id": d.id}, audience="insurer"))
        future = [x for x in found if x > ctx.today]
        if future:
            r.add("warning", Finding("document_date_in_future", {"en": f"{d.filename} contains a future date ({future[0].isoformat()}).", "zh": f"{d.filename} 包含未来日期（{future[0].isoformat()}）。"}, {"document_id": d.id, "date": future[0].isoformat()}, severity="medium"))
        if d.doc_type in {"receipt", "invoice", "estimate"} and found:
            earliest = min(found)
            if earliest < c.incident_date and d.doc_type != "estimate":
                # a purchase receipt (proof of ownership) legitimately predates the incident
                if d.checklist_item_key in {"proof_of_ownership", "purchase_receipt", "receipts"}:
                    continue
                r.add("warning", Finding("document_predates_incident", {"en": f"{d.filename} is dated {earliest.isoformat()}, before the incident on {c.incident_date.isoformat()}. If it is a repair or treatment invoice, a date before the incident needs an explanation.", "zh": f"{d.filename} 的日期为 {earliest.isoformat()}，早于事件日期 {c.incident_date.isoformat()}。如为维修或治疗发票，需要说明原因。"}, {"document_id": d.id, "document_date": earliest.isoformat()}, severity="medium"))
    return r


def check_documents(ctx: Context) -> CheckerResult:
    r = CheckerResult("document_integrity", "1.0", confidence="medium")
    r.note = {"en": "No anomaly detected does not establish that a file is authentic; it means these structural checks found nothing.", "zh": "“未发现异常”并不代表文件真实，只表示这些结构性检查没有发现问题。"}
    seen_hashes: dict[str, Document] = {}
    for d in ctx.documents:
        head = _read_head(d)
        if not head:
            r.add("inconclusive", Finding("file_unreadable", {"en": f"{d.filename} could not be read from storage.", "zh": f"无法从存储中读取 {d.filename}。"}, {"document_id": d.id}, audience="insurer"))
            continue
        if d.mime_type == "application/pdf":
            if not head.startswith(b"%PDF-"):
                r.add("failed_validation", Finding("pdf_header_mismatch", {"en": f"{d.filename} is declared as PDF but does not start with a PDF header.", "zh": f"{d.filename} 声明为 PDF，但文件头不是 PDF。"}, {"document_id": d.id}, severity="high"))
                continue
            data = _read_all(d)
            if b"%%EOF" not in data[-2048:]:
                r.add("warning", Finding("pdf_truncated", {"en": f"{d.filename} has no end of file marker; it may be truncated or altered.", "zh": f"{d.filename} 缺少文件结束标记，可能被截断或修改。"}, {"document_id": d.id}, severity="medium", audience="insurer"))
            updates = data.count(b"startxref")
            if updates > 1:
                r.add("human_review_required", Finding("pdf_incremental_updates", {"en": f"{d.filename} was saved {updates - 1} time(s) after creation (incremental updates). This is common with annotation tools and also with edits; unable to assess which.", "zh": f"{d.filename} 在创建后被保存了 {updates - 1} 次（增量更新）。标注工具和编辑都会产生这种情况，无法判断。"}, {"document_id": d.id, "incremental_updates": updates - 1}, severity="medium", audience="insurer"))
            pages = len(re.findall(rb"/Type\s*/Page[^s]", data))
            if pages == 0:
                r.add("inconclusive", Finding("pdf_no_pages", {"en": f"No page objects were found in {d.filename}.", "zh": f"在 {d.filename} 中未找到页面对象。"}, {"document_id": d.id}, audience="insurer"))
            m_c = re.search(rb"/CreationDate\s*\(D:(\d{8})", data)
            m_m = re.search(rb"/ModDate\s*\(D:(\d{8})", data)
            if m_c and m_m and m_m.group(1) != m_c.group(1):
                r.add("warning", Finding("pdf_modified_after_creation", {"en": f"{d.filename} records a modification date different from its creation date.", "zh": f"{d.filename} 的修改日期与创建日期不同。"}, {"document_id": d.id, "created": m_c.group(1).decode(), "modified": m_m.group(1).decode()}, severity="low", audience="insurer"))
            r.note[f"{d.id}"] = {"pages": pages, "incremental_updates": max(0, updates - 1)}
        elif d.mime_type.startswith("image/"):
            sig_ok = head.startswith(b"\x89PNG\r\n\x1a\n") if d.mime_type == "image/png" else head.startswith(b"\xff\xd8") if d.mime_type == "image/jpeg" else head[:4] == b"RIFF" if d.mime_type == "image/webp" else True
            if not sig_ok:
                r.add("failed_validation", Finding("image_signature_mismatch", {"en": f"{d.filename} is declared as {d.mime_type} but its signature does not match.", "zh": f"{d.filename} 声明为 {d.mime_type}，但文件签名不符。"}, {"document_id": d.id}, severity="high"))
                continue
            if d.mime_type == "image/jpeg" and b"Exif" not in head:
                r.add("inconclusive", Finding("image_no_metadata", {"en": f"{d.filename} carries no camera metadata, so when and where it was taken cannot be assessed.", "zh": f"{d.filename} 不含相机元数据，无法判断拍摄时间和地点。"}, {"document_id": d.id}, audience="insurer"))
            if d.mime_type == "image/png" and len(head) >= 24:
                w, h = struct.unpack(">II", head[16:24])
                if w < 64 or h < 64:
                    r.add("warning", Finding("image_tiny", {"en": f"{d.filename} is only {w}×{h} pixels; it may not show the damage clearly.", "zh": f"{d.filename} 仅 {w}×{h} 像素，可能无法清晰显示损失。"}, {"document_id": d.id}, severity="low"))
        actual = hashlib.sha256(_read_all(d)).hexdigest()
        if actual != d.sha256:
            r.add("failed_validation", Finding("stored_hash_mismatch", {"en": f"The stored hash of {d.filename} does not match the file on disk.", "zh": f"{d.filename} 的存储哈希与磁盘文件不一致。"}, {"document_id": d.id}, severity="high", audience="insurer"))
        if d.sha256 in seen_hashes:
            r.add("warning", Finding("identical_upload_in_claim", {"en": f"{d.filename} is identical to {seen_hashes[d.sha256].filename} already attached to this claim.", "zh": f"{d.filename} 与本理赔中已有的 {seen_hashes[d.sha256].filename} 完全相同。"}, {"document_id": d.id, "duplicate_of": seen_hashes[d.sha256].id}, severity="low"))
        seen_hashes.setdefault(d.sha256, d)
    return r


def check_duplicates(ctx: Context) -> CheckerResult:
    r = CheckerResult("duplicate_evidence", "1.0", confidence="high")
    db, c = ctx.db, ctx.claim
    if not ctx.documents:
        r.add("inconclusive", Finding("no_documents", {"en": "No documents to compare yet.", "zh": "尚无可比对的文件。"}, audience="customer"))
        return r
    hashes = [d.sha256 for d in ctx.documents]
    # exact duplicates: the same bytes supporting a different claim
    others = db.execute(select(Document).where(Document.sha256.in_(hashes), Document.claim_id.isnot(None), Document.claim_id != c.id)).scalars().all()
    for o in others:
        other_claim = db.get(Claim, o.claim_id)
        same_customer = other_claim is not None and other_claim.user_id == c.user_id
        mine = next(d for d in ctx.documents if d.sha256 == o.sha256)
        if o.doc_type == "policy_wording" or mine.doc_type == "policy_wording":
            continue
        r.add("human_review_required", Finding("identical_file_other_claim", {"en": f"{mine.filename} is byte for byte identical to a document on another claim ({other_claim.reference if other_claim and same_customer else 'a different customer'}). A repeated invoice can be legitimate; a person should check it is not claimed twice.", "zh": f"{mine.filename} 与另一理赔（{other_claim.reference if other_claim and same_customer else '另一位客户'}）中的文件完全相同。重复的发票可能是正常的，需人工确认未重复索赔。"}, {"document_id": mine.id, "other_claim_id": o.claim_id, "same_customer": same_customer}, severity="high", audience="insurer" if not same_customer else "both"))
    # near duplicates: invoice numbers, totals and text similarity across the customer's other claims
    my_claim_ids = [x for (x,) in db.execute(select(Claim.id).where(Claim.user_id == c.user_id, Claim.id != c.id))]
    other_docs = db.execute(select(Document).where(Document.claim_id.in_(my_claim_ids), Document.extracted_text.isnot(None))).scalars().all() if my_claim_ids else []
    for d in ctx.documents:
        if not d.extracted_text or d.doc_type == "policy_wording":
            continue
        my_refs = {_normalise_ref(m.group(1)) for m in INVOICE_NO.finditer(d.extracted_text)}
        my_total = TOTAL.search(d.extracted_text)
        my_sh = _shingles(d.extracted_text)
        for o in other_docs:
            if o.sha256 == d.sha256:
                continue
            o_refs = {_normalise_ref(m.group(1)) for m in INVOICE_NO.finditer(o.extracted_text or "")}
            shared = {x for x in (my_refs & o_refs) if len(x) >= 4}
            o_claim = db.get(Claim, o.claim_id)
            if shared:
                r.add("human_review_required", Finding("invoice_reference_reused", {"en": f"{d.filename} shares reference {sorted(shared)[0]} with {o.filename} on claim {o_claim.reference if o_claim else ''}.", "zh": f"{d.filename} 与理赔 {o_claim.reference if o_claim else ''} 中的 {o.filename} 使用了相同的编号 {sorted(shared)[0]}。"}, {"document_id": d.id, "other_document_id": o.id, "reference": sorted(shared)[0]}, severity="high"))
                continue
            sim = _jaccard(my_sh, _shingles(o.extracted_text or ""))
            if sim >= 0.8:
                o_total = TOTAL.search(o.extracted_text or "")
                same_total = bool(my_total and o_total and my_total.group(1) == o_total.group(1))
                r.add("human_review_required" if same_total else "warning", Finding("near_duplicate_document", {"en": f"{d.filename} is {int(sim * 100)}% similar to {o.filename} on claim {o_claim.reference if o_claim else ''}{' with the same total' if same_total else ''}.", "zh": f"{d.filename} 与理赔 {o_claim.reference if o_claim else ''} 中的 {o.filename} 相似度 {int(sim * 100)}%{'，金额相同' if same_total else ''}。"}, {"document_id": d.id, "other_document_id": o.id, "similarity": round(sim, 3), "same_total": same_total}, severity="medium", audience="insurer"))
    return r


def check_amounts(ctx: Context) -> CheckerResult:
    r = CheckerResult("amount_plausibility", "1.0", confidence="high")
    c, p = ctx.claim, ctx.policy
    if c.estimated_loss is not None and c.estimated_loss < 0:
        r.add("failed_validation", Finding("negative_loss", {"en": "The estimated loss is negative.", "zh": "预计损失为负数。"}, severity="high"))
    if p and c.currency and p.currency and c.currency != p.currency:
        r.add("warning", Finding("currency_mismatch", {"en": f"The claim is in {c.currency} but the policy is in {p.currency}.", "zh": f"理赔币种为 {c.currency}，而保单币种为 {p.currency}。"}, {"claim_currency": c.currency, "policy_currency": p.currency}, severity="medium"))
    items = [i for i in (c.items_involved or []) if isinstance(i, dict)]
    values = []
    for i in items:
        try:
            if i.get("value") not in (None, ""):
                values.append(float(i["value"]))
        except (TypeError, ValueError):
            r.add("warning", Finding("item_value_not_numeric", {"en": f"The value of '{i.get('label', 'an item')}' is not a number.", "zh": f"“{i.get('label', '物品')}”的价值不是数字。"}, {"item": i.get("label")}, severity="low", audience="customer"))
    if values and c.estimated_loss is not None and not c.loss_unknown:
        total = sum(values)
        if total > 0 and abs(total - c.estimated_loss) > max(1.0, 0.01 * max(total, c.estimated_loss)):
            r.add("warning", Finding("items_do_not_sum", {"en": f"The items listed add up to {total:,.2f} but the estimated loss is {c.estimated_loss:,.2f}.", "zh": f"所列物品合计 {total:,.2f}，但预计损失为 {c.estimated_loss:,.2f}。"}, {"items_total": round(total, 2), "estimated_loss": c.estimated_loss}, severity="medium"))
    # invoice arithmetic: amounts before the total should add up to the total
    for d in ctx.documents:
        if d.doc_type not in {"receipt", "invoice", "estimate"} or not d.extracted_text:
            continue
        m = TOTAL.search(d.extracted_text)
        if not m:
            r.add("inconclusive", Finding("no_total_found", {"en": f"No total could be read from {d.filename}; arithmetic not checked.", "zh": f"无法从 {d.filename} 读取合计金额，未检查算术。"}, {"document_id": d.id}, audience="insurer"))
            continue
        try:
            total = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        parts = [v for v in _amounts_in(d.extracted_text[: m.start()]) if v != total]
        if len(parts) >= 2:
            s = sum(parts)
            if abs(s - total) > 0.01 and abs(s - total) > 0.005 * total:
                r.add("warning", Finding("invoice_arithmetic_mismatch", {"en": f"In {d.filename} the line amounts add up to {s:,.2f} but the total reads {total:,.2f}. This can be a reading error of the document text; a person should check the original.", "zh": f"{d.filename} 中各项金额合计 {s:,.2f}，但总计为 {total:,.2f}。可能是文本读取误差，需人工核对原件。"}, {"document_id": d.id, "line_sum": round(s, 2), "total": total}, severity="medium"))
        if c.estimated_loss and total > c.estimated_loss * 1.5:
            r.add("warning", Finding("invoice_exceeds_estimate", {"en": f"{d.filename} totals {total:,.2f}, well above the estimated loss of {c.estimated_loss:,.2f}.", "zh": f"{d.filename} 总计 {total:,.2f}，明显高于预计损失 {c.estimated_loss:,.2f}。"}, {"document_id": d.id, "total": total, "estimated_loss": c.estimated_loss}, severity="low"))
    # policy terms (information, not judgement)
    if p and c.estimated_loss:
        if p.excess_amount is not None and c.estimated_loss <= p.excess_amount:
            r.add("warning", Finding("below_excess", {"en": f"The estimated loss ({c.estimated_loss:,.0f}) is not above the policy excess ({p.excess_amount:,.0f}); a payment may not result.", "zh": f"预计损失（{c.estimated_loss:,.0f}）未超过免赔额（{p.excess_amount:,.0f}），可能不会产生赔付。"}, {"excess": p.excess_amount}, severity="low", audience="customer"))
        limits = [f for f in ctx.facts if f.fact_type == "limit" and f.value and f.status == "known"]
        if limits:
            cap = max(f.value for f in limits)
            if c.estimated_loss > cap:
                r.add("warning", Finding("above_stated_limits", {"en": f"The estimated loss ({c.estimated_loss:,.0f}) exceeds the highest limit stated in the wording ({cap:,.0f}).", "zh": f"预计损失（{c.estimated_loss:,.0f}）超过条款写明的最高限额（{cap:,.0f}）。"}, {"limit": cap}, severity="low"))
    return r


def check_injection(ctx: Context) -> CheckerResult:
    r = CheckerResult("prompt_injection", "1.0", confidence="high")
    r.note = {"en": "Instruction like text in a document is data. It is never executed, never changes permissions and does not invalidate the document.", "zh": "文件中类似指令的文字只是数据，不会被执行，不会改变权限，也不会使文件失效。"}
    for d in ctx.documents:
        flags = d.injection_flags or []
        if flags:
            r.add("warning", Finding("document_instruction_text", {"en": f"{d.filename} contains instruction like text ({', '.join(str(f.get('type', f)) for f in flags[:3])}). It was quarantined from model context and cannot trigger an action.", "zh": f"{d.filename} 含有类似指令的文字（{', '.join(str(f.get('type', f)) for f in flags[:3])}），已与模型上下文隔离，不会触发任何操作。"}, {"document_id": d.id, "flags": flags[:5]}, severity="medium"))
    for label, text in (("description", ctx.claim.description), ("actions_taken", ctx.claim.actions_taken)):
        res = scan_for_injection(text)
        if res.suspicious:
            r.add("warning", Finding("claim_text_instruction_like", {"en": f"The claim {label.replace('_', ' ')} contains instruction like text; it is treated as the customer's statement only.", "zh": f"理赔{('经过' if label == 'description' else '已采取措施')}中含有类似指令的文字，仅作为客户陈述处理。"}, {"field": label, "flags": [f.get("type", f) if isinstance(f, dict) else str(f) for f in res.flags[:3]]}, severity="low", audience="insurer"))
    return r


def check_consistency(ctx: Context) -> CheckerResult:
    r = CheckerResult("cross_document_consistency", "1.0", confidence="medium")
    c = ctx.claim
    texts = {d.id: d for d in ctx.documents if d.extracted_text}
    if not texts:
        r.add("inconclusive", Finding("no_readable_text", {"en": "No document text is available to compare (images without OCR).", "zh": "没有可比对的文件文字（图片未做 OCR）。"}, audience="insurer"))
        return r
    # police / authority reference should appear in a police report document
    if c.authorities_involved and c.authority_reference:
        reports = [d for d in texts.values() if d.doc_type in {"police_report", "correspondence"}]
        if reports:
            want = _normalise_ref(c.authority_reference)
            if not any(want and want in _normalise_ref(d.extracted_text or "") for d in reports):
                r.add("warning", Finding("authority_reference_not_in_report", {"en": f"The reference '{c.authority_reference}' was not found in the uploaded report. It may be a transcription difference.", "zh": f"在上传的报案材料中未找到编号“{c.authority_reference}”，可能是抄写差异。"}, {"authority_reference": c.authority_reference, "documents": [d.id for d in reports]}, severity="medium"))
    elif c.authorities_involved and not c.authority_reference:
        r.add("warning", Finding("authority_reference_missing", {"en": "The incident was reported to the authorities but no reference number is recorded.", "zh": "事件已报案，但未记录报案编号。"}, severity="low", audience="customer"))
    # amounts in invoices versus the item list
    item_values = {round(float(i["value"]), 2) for i in (c.items_involved or []) if isinstance(i, dict) and i.get("value") not in (None, "")}
    for d in texts.values():
        if d.doc_type not in {"receipt", "invoice", "estimate"}:
            continue
        amounts = set(round(v, 2) for v in _amounts_in(d.extracted_text or ""))
        if item_values and amounts and not (item_values & amounts) and not (c.estimated_loss and round(c.estimated_loss, 2) in amounts):
            r.add("warning", Finding("invoice_amounts_not_in_item_list", {"en": f"None of the amounts in {d.filename} match the values in the item list; the figures may describe different things.", "zh": f"{d.filename} 中的金额与物品清单中的数值均不一致，可能描述的是不同事项。"}, {"document_id": d.id}, severity="low"))
    # incident date mentioned in documents should not contradict the recorded one by a wide margin
    if c.incident_date:
        for d in texts.values():
            if d.doc_type not in {"police_report", "correspondence", "medical"}:
                continue
            found, _ = _dates_in(d.extracted_text or "")
            close = [x for x in found if abs((x - c.incident_date).days) <= 7]
            if found and not close:
                r.add("warning", Finding("report_dates_differ", {"en": f"The dates in {d.filename} ({', '.join(x.isoformat() for x in sorted(found)[:3])}) are more than a week away from the recorded incident date {c.incident_date.isoformat()}.", "zh": f"{d.filename} 中的日期（{', '.join(x.isoformat() for x in sorted(found)[:3])}）与记录的事件日期 {c.incident_date.isoformat()} 相差超过一周。"}, {"document_id": d.id}, severity="medium"))
    return r


def check_provenance(ctx: Context) -> CheckerResult:
    r = CheckerResult("evidence_provenance", "1.0", confidence="high")
    c = ctx.claim
    if ctx.policy and ctx.policy.user_id != c.user_id:
        r.add("failed_validation", Finding("policy_not_customers", {"en": "The linked policy does not belong to the claimant.", "zh": "关联的保单不属于申请人。"}, severity="high", audience="insurer"))
    for d in ctx.documents:
        if d.owner_user_id != c.user_id:
            r.add("failed_validation", Finding("document_not_customers", {"en": f"{d.filename} belongs to a different account.", "zh": f"{d.filename} 属于另一账户。"}, {"document_id": d.id}, severity="high", audience="insurer"))
        if d.extraction_status == "failed":
            r.add("inconclusive", Finding("extraction_failed", {"en": f"The text of {d.filename} could not be extracted; it was not checked.", "zh": f"无法提取 {d.filename} 的文字，未作检查。"}, {"document_id": d.id}, audience="insurer"))
    sources = {"incident_date": "customer", "location": "customer", "description": "customer", "items_involved": "customer", "estimated_loss": "customer" if not c.loss_unknown else "unknown", "coverage_view": "platform_rules", "checklist": "platform_rules", "completeness": "platform_rules"}
    r.note = {"field_sources": sources, "documents_reviewed_by_insurer": sum(1 for d in ctx.documents if d.reviewed_by_insurer), "documents": len(ctx.documents)}
    missing = list((c.completeness or {}).get("missing_required_documents", [])) + list((c.completeness or {}).get("missing_fields", []))
    if missing:
        r.add("warning", Finding("required_items_missing", {"en": "Required items are still missing: " + ", ".join(missing[:6]), "zh": "仍缺少必需项：" + "、".join(missing[:6])}, {"missing": missing}, severity="low", audience="customer"))
    inferred = [k for k, v in sources.items() if v == "platform_rules"]
    r.note["inferred_not_provided"] = inferred
    return r


CHECKERS: list[Callable[[Context], CheckerResult]] = [check_dates, check_documents, check_duplicates, check_amounts, check_injection, check_consistency, check_provenance]


# ------------------------------------------------------------- aggregation ---


def aggregate(results: list[CheckerResult]) -> tuple[str, list[dict]]:
    statuses = {r.status for r in results}
    for s in STATUS_ORDER:
        if s in statuses:
            overall = s
            break
    else:
        overall = "passed"
    escalations = []
    for r in results:
        if r.status in {"failed_validation", "human_review_required"}:
            for f in r.findings:
                if f.severity in {"medium", "high"}:
                    escalations.append({"checker": r.checker_id, "code": f.code, "severity": f.severity, "message": f.message, "evidence": f.evidence, "audience": f.audience})
    return overall, escalations


def _inputs_hash(ctx: Context) -> str:
    base = {"claim": [ctx.claim.incident_date.isoformat() if ctx.claim.incident_date else None, ctx.claim.estimated_loss, ctx.claim.items_involved, ctx.claim.authority_reference, ctx.claim.authorities_involved, ctx.claim.policy_id, ctx.claim.description, ctx.claim.currency], "docs": sorted((d.id, d.sha256, d.doc_type, d.checklist_item_key or "") for d in ctx.documents), "version": ENSEMBLE_VERSION}
    return hashlib.sha256(json.dumps(base, sort_keys=True, default=str).encode()).hexdigest()


def run(db: Session, claim: Claim, *, today: date | None = None, force: bool = False) -> ClaimCheckResult:
    """Run every checker, aggregate deterministically and persist. A checker
    that raises produces an ``inconclusive`` result, never an invented pass."""
    policy = db.get(Policy, claim.policy_id) if claim.policy_id else None
    clauses = list(policy.clauses) if policy else []
    facts = db.execute(select(PolicyFact).where(PolicyFact.policy_id == policy.id)).scalars().all() if policy else []
    docs = list(db.execute(select(Document).where(Document.claim_id == claim.id).order_by(Document.created_at)).scalars().all())
    owner = db.get(User, claim.user_id)
    ctx = Context(db=db, claim=claim, policy=policy, clauses=clauses, facts=list(facts), documents=docs, owner=owner, today=today or date.today())
    h = _inputs_hash(ctx)
    latest = db.execute(select(ClaimCheckResult).where(ClaimCheckResult.claim_id == claim.id).order_by(ClaimCheckResult.created_at.desc()).limit(1)).scalar_one_or_none()
    if latest and latest.inputs_hash == h and not force:
        return latest
    results: list[CheckerResult] = []
    for fn in CHECKERS:
        try:
            results.append(fn(ctx))
        except Exception as exc:  # a failed checker is inconclusive, never a pass
            r = CheckerResult(fn.__name__.replace("check_", ""), "1.0", status="inconclusive", confidence="low")
            r.findings.append(Finding("checker_error", {"en": f"This check could not run: {type(exc).__name__}.", "zh": f"该检查无法运行：{type(exc).__name__}。"}, {"error": str(exc)[:200], "trace": traceback.format_exc()[-400:]}, severity="low", audience="insurer"))
            results.append(r)
    overall, escalations = aggregate(results)
    row = ClaimCheckResult(claim_id=claim.id, overall_status=overall, checkers=[r.to_dict() for r in results], escalations=escalations, inputs_hash=h)
    db.add(row)
    db.flush()
    return row


def latest(db: Session, claim: Claim) -> ClaimCheckResult | None:
    return db.execute(select(ClaimCheckResult).where(ClaimCheckResult.claim_id == claim.id).order_by(ClaimCheckResult.created_at.desc()).limit(1)).scalar_one_or_none()


def to_dict(row: ClaimCheckResult | None, *, audience: str) -> dict | None:
    """Audience filtered view: customers see what they can fix or explain;
    insurer staff see everything including cross customer matches."""
    if row is None:
        return None
    checkers = []
    for ch in row.checkers:
        fs = [f for f in ch["findings"] if audience == "insurer" or f.get("audience") in {"customer", "both"}]
        checkers.append({**ch, "findings": fs, "note": ch.get("note") if audience == "insurer" else {k: v for k, v in (ch.get("note") or {}).items() if k in {"en", "zh"}}})
    esc = [e for e in row.escalations if audience == "insurer" or e.get("audience") in {"customer", "both"}]
    return {"id": row.id, "overall_status": row.overall_status, "checkers": checkers, "escalations": esc, "created_at": row.created_at.isoformat(), "ensemble_version": ENSEMBLE_VERSION, "disclaimer": {"en": "Automated checks flag items for a person to look at. They never decide a claim or accuse anyone; no anomaly detected is not proof of authenticity.", "zh": "自动检查仅提示需要人工查看的事项，不会作出理赔结论或指控任何人；“未发现异常”也不是真实性的证明。"}}


def blocking_reasons(row: ClaimCheckResult | None) -> list[str]:
    if not row or row.overall_status != "failed_validation":
        return []
    out = []
    for ch in row.checkers:
        if ch["status"] == "failed_validation":
            out += [f["message"]["en"] for f in ch["findings"]]
    return out


__all__ = ["run", "latest", "to_dict", "blocking_reasons", "aggregate", "CheckerResult", "Finding", "Context", "CHECKERS", "ENSEMBLE_VERSION", "utcnow", "datetime"]
