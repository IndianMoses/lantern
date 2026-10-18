"""Document ingestion: validation, text extraction, prompt injection scan,
clause segmentation and metadata guesses for uploaded policy wordings.

Extracted text is always treated as untrusted data. The scanner does not
block uploads (a policy can legitimately contain the word "instructions");
it records flags that the agent layer, the interface and the audit trail
can see, and anything flagged is wrapped and labelled before a model sees
it.
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field

from .retrieval import detect_language, extract_tags

ALLOWED_MIME = {
    "application/pdf": {".pdf"},
    "text/plain": {".txt", ".md"},
    "image/png": {".png"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/webp": {".webp"},
}
MAGIC = {
    b"%PDF": "application/pdf",
    b"\x89PNG": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"RIFF": "image/webp",
}


class ValidationError(ValueError):
    pass


def sniff_mime(filename: str, declared: str | None, head: bytes) -> str:
    for magic, mime in MAGIC.items():
        if head.startswith(magic):
            return mime
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {".txt", ".md"}:
        try:
            head.decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError as exc:
            raise ValidationError("text files must be UTF-8") from exc
    raise ValidationError("unsupported file type; upload PDF, PNG, JPEG, WEBP or plain text")


def validate_upload(filename: str, declared_mime: str | None, data: bytes, max_bytes: int) -> str:
    if not data:
        raise ValidationError("empty file")
    if len(data) > max_bytes:
        raise ValidationError(f"file is larger than {max_bytes // (1024 * 1024)} MB")
    if "\x00" in filename or "/" in filename or "\\" in filename:
        raise ValidationError("invalid file name")
    mime = sniff_mime(filename, declared_mime, data[:16])
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext and ext not in ALLOWED_MIME[mime]:
        raise ValidationError("file extension does not match its content")
    return mime


# ------------------------------------------------------------- extraction ---


def extract_text(mime: str, data: bytes) -> tuple[str | None, dict]:
    meta: dict = {}
    if mime == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pages = []
            for i, page in enumerate(reader.pages):
                try:
                    pages.append(page.extract_text() or "")
                except Exception:  # pragma: no cover - malformed page
                    pages.append("")
            meta["pages"] = len(pages)
            text = "\n\n".join(f"[[page {i + 1}]]\n{t}" for i, t in enumerate(pages))
            if not any(p.strip() for p in pages):
                meta["note"] = "no text layer; OCR not configured in this environment"
                return None, meta
            return text, meta
        except Exception as exc:  # pragma: no cover
            meta["error"] = str(exc)
            return None, meta
    if mime == "text/plain":
        return data.decode("utf-8", errors="replace"), meta
    meta["note"] = "image uploaded; no OCR configured in this environment"
    return None, meta


# ------------------------------------------------------- injection scanner ---

INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore (all|any|the|your)? ?(previous|prior|above|earlier) (instructions|rules|prompts?)", "instruction_override"),
    (r"disregard (all|any|the|your)? ?(previous|prior|above) (instructions|rules)", "instruction_override"),
    (r"\bsystem prompt\b", "system_prompt_reference"),
    (r"you are now\b", "persona_override"),
    (r"\bas an? (ai|assistant|language model)\b.*\b(must|should|will)\b", "role_coercion"),
    (r"\b(submit|approve|send|pay|transfer|authori[sz]e)\b.{0,40}\b(claim|payment|money|funds|immediately|now|without)\b", "action_coercion"),
    (r"\bdo not (tell|inform|show) (the )?(user|customer)\b", "concealment"),
    (r"\btool(_| )?call\b|\bfunction(_| )?call\b|<tool", "tool_reference"),
    (r"\bassistant:\s", "transcript_spoof"),
    (r"忽略(以上|之前|先前|前面)?(的)?(所有)?(指令|指示|规则|提示)", "instruction_override"),
    (r"系统提示", "system_prompt_reference"),
    (r"你现在是", "persona_override"),
    (r"(立即|马上|直接)(提交|批准|通过|支付|转账)", "action_coercion"),
    (r"不要(告诉|通知)(用户|客户)", "concealment"),
]
_COMPILED = [(re.compile(p, re.I | re.S), label) for p, label in INJECTION_PATTERNS]
_ZERO_WIDTH = re.compile(r"[​‌‍⁠﻿]")


@dataclass
class ScanResult:
    flags: list[dict] = field(default_factory=list)

    @property
    def suspicious(self) -> bool:
        return bool(self.flags)


def scan_for_injection(text: str | None) -> ScanResult:
    result = ScanResult()
    if not text:
        return result
    zw = len(_ZERO_WIDTH.findall(text))
    if zw > 3:
        result.flags.append({"type": "hidden_characters", "count": zw, "excerpt": ""})
    for rx, label in _COMPILED:
        for m in rx.finditer(text):
            start = max(0, m.start() - 40)
            excerpt = text[start : m.end() + 40].replace("\n", " ")
            result.flags.append({"type": label, "excerpt": excerpt[:160]})
            if len(result.flags) > 20:
                return result
    return result


def sanitise_for_model(text: str, *, max_chars: int = 6000) -> str:
    """Remove control characters and clip; the caller wraps the result in a
    labelled untrusted block."""
    cleaned = "".join(ch for ch in text if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C")
    cleaned = _ZERO_WIDTH.sub("", cleaned)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + f"\n[... {len(cleaned) - max_chars} characters omitted ...]"
    return cleaned


# ---------------------------------------------------- clause segmentation ---

_EN_HEADING = re.compile(r"^(?:(?:section|part)\s+)?(\d{1,2}(?:\.\d{1,2}){0,2})[\s.):-]+(.{2,120})$", re.I)
_ZH_HEADING = re.compile(r"^第([一二三四五六七八九十百\d]{1,4})(条|章|节)[\s、:：]*(.{0,80})$")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

EXCL_KW = ("not cover", "not pay", "excluded", "exclusion", "we will not", "does not cover", "no cover", "不承担", "不负责", "除外", "免责", "不予赔偿", "不属于")
COND_KW = ("you must", "must", "condition", "required to", "within", "应当", "须", "必须", "条件", "之内")
LIMIT_KW = ("excess", "deductible", "most we will pay", "limit", "免赔", "限额", "最高")
PROC_KW = ("to make a claim", "how to claim", "claims procedure", "notify", "report", "索赔", "理赔", "报案", "通知保险人")
DEF_KW = ("means", "definition", "定义", "是指", "称为")


def _cn_to_int(s: str) -> str:
    if s.isdigit():
        return s
    total = 0
    if s.startswith("十"):
        s = "一" + s
    if "十" in s:
        tens, _, ones = s.partition("十")
        total = _CN_NUM.get(tens, 0) * 10 + (_CN_NUM.get(ones, 0) if ones else 0)
    else:
        total = _CN_NUM.get(s, 0)
    return str(total or s)


def classify_kind(title: str, text: str) -> str:
    blob = f"{title} {text}".lower()
    if any(k in blob for k in EXCL_KW):
        return "exclusion"
    if any(k in blob for k in PROC_KW):
        return "claims_procedure"
    if any(k in blob for k in DEF_KW) and len(text) < 600:
        return "definition"
    if any(k in blob for k in LIMIT_KW) and len(text) < 500:
        return "limit"
    if any(k in blob for k in COND_KW):
        return "condition"
    return "coverage"


def segment_clauses(text: str) -> list[dict]:
    """Split a wording into numbered clauses. Falls back to paragraph
    chunks when no numbering is found. Confidence reflects which path ran."""
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
    clauses: list[dict] = []
    current: dict | None = None
    section = "General"
    page = 1
    for ln in lines:
        if not ln:
            continue
        pm = re.match(r"^\[\[page (\d+)\]\]$", ln)
        if pm:
            page = int(pm.group(1))
            continue
        if re.match(r"^(section|part)\s+\d+\b", ln, re.I) or re.match(r"^第[一二三四五六七八九十\d]+章", ln):
            section = ln[:160]
        m = _EN_HEADING.match(ln) or _ZH_HEADING.match(ln)
        if m:
            if current:
                clauses.append(current)
            if m.re is _ZH_HEADING:
                ref = _cn_to_int(m.group(1))
                title = (m.group(3) or f"第{m.group(1)}{m.group(2)}").strip()[:200]
                if m.group(2) == "章":
                    section = ln[:160]
                    current = None
                    continue
            else:
                ref = m.group(1)
                title = m.group(2).strip()[:200]
                if "." not in ref and len(title) < 60 and ln.lower().startswith(("section", "part")):
                    section = ln[:160]
                    current = None
                    continue
            current = {"ref": ref, "section": section, "title": title, "text": "", "page": page}
        else:
            if current is None:
                current = {"ref": str(len(clauses) + 1), "section": section, "title": ln[:80], "text": "", "page": page}
            current["text"] = (current["text"] + " " + ln).strip()
    if current:
        clauses.append(current)
    out = []
    for c in clauses:
        body = c["text"] or c["title"]
        if len(body) < 15:
            continue
        kind = classify_kind(c["title"], body)
        tags = sorted(extract_tags(f"{c['title']} {body}"))
        amount = _first_amount(body)
        out.append({**c, "text": body[:4000], "kind": kind, "tags": tags, "amount": amount, "confidence": 0.65, "language": detect_language(body)})
    return out


def _first_amount(text: str) -> float | None:
    m = re.search(r"(?:£|¥|\$|RMB|人民币)\s?([\d,]+(?:\.\d+)?)", text)
    if not m:
        m = re.search(r"([\d,]+(?:\.\d+)?)\s?(?:元|英镑|美元)", text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def guess_metadata(text: str, filename: str) -> dict:
    meta: dict = {"source_filename": filename}
    pn = re.search(r"(?:policy (?:number|no\.?)|保单号|保险单号)[:：\s]*([A-Z0-9][A-Z0-9\-/]{4,})", text, re.I)
    if pn:
        meta["policy_number"] = pn.group(1)
    dates = re.findall(r"(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2})", text)
    if dates:
        meta["dates_found"] = dates[:4]
    prov = re.search(r"(?:provider|insurer|underwritten by|保险人|保险公司)[:：\s]*([^\n,;。]{2,60})", text, re.I)
    if prov:
        meta["provider_name"] = prov.group(1).strip()
    low = text.lower()
    type_hits = {
        "motor": sum(k in low for k in ("vehicle", "windscreen", "driver", "车辆", "机动车")),
        "travel": sum(k in low for k in ("trip", "baggage", "repatriation", "旅行", "行李")),
        "pet": sum(k in low for k in ("vet", "pet", "宠物")),
        "ebike": sum(k in low for k in ("electric bicycle", "e-bike", "电动自行车")),
        "gadget": sum(k in low for k in ("screen", "imei", "碎屏", "手机")),
        "renters": sum(k in low for k in ("tenant", "landlord", "租客", "房东")),
        "home": sum(k in low for k in ("buildings", "contents", "home", "家财", "房屋")),
    }
    best = max(type_hits.items(), key=lambda kv: kv[1])
    meta["insurance_type_guess"] = best[0] if best[1] else "home"
    meta["language"] = detect_language(text)
    return meta
