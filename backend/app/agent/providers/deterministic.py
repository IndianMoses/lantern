"""Deterministic, fully offline provider.

It plans tool calls from the classified intent and composes answers only
from tool outputs, so every statement it makes is grounded by
construction. It exists so that tests, the sandbox evidence run and demos
work without any model credentials, and it is the fallback when the model
provider cannot be reached.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from typing import Any

from ...services.claims import STATUS_LABELS
from ...services.taxonomy import LABELS, incident_label, insurance_label
from ..intent import Intent, classify
from .base import ProviderTurn, ToolCall


def _tc(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"det_{uuid.uuid4().hex[:10]}", name=name, arguments=arguments)


def _last_user_text(messages: list[dict]) -> tuple[int, str]:
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m["role"] != "user":
            continue
        texts = [b["text"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "text"]
        if texts:
            return i, "\n".join(texts)
    return -1, ""


def _done_calls(messages: list[dict], start: int) -> list[tuple[str, dict, dict]]:
    calls: dict[str, tuple[str, dict]] = {}
    out: list[tuple[str, dict, dict]] = []
    for m in messages[start + 1 :]:
        for b in m["content"]:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                calls[b["id"]] = (b["name"], b.get("input") or {})
            elif b.get("type") == "tool_result":
                name, args = calls.get(b["tool_use_id"], ("?", {}))
                try:
                    result = json.loads(b.get("content") or "{}")
                except json.JSONDecodeError:
                    result = {"raw": b.get("content")}
                out.append((name, args, result))
    return out


def _parse_money(text: str) -> float | None:
    m = re.search(r"(?:£|¥|\$|rmb|人民币)\s?([\d,]+(?:\.\d+)?)", text, re.I) or re.search(r"([\d,]+(?:\.\d+)?)\s?(?:元|块|pounds|quid|gbp|dollars|usd)", text, re.I)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _parse_date(text: str) -> date | None:
    today = date.today()
    low = text.lower()
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", low)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    if re.search(r"\b(today|this morning|tonight|just now)\b|今天|今早|刚刚", low):
        return today
    if re.search(r"\b(yesterday|last night)\b|昨天|昨晚", low):
        return today - timedelta(days=1)
    if re.search(r"day before yesterday|前天", low):
        return today - timedelta(days=2)
    m = re.search(r"\b(\d+) days? ago\b|(\d+)天前", low)
    if m:
        return today - timedelta(days=int(m.group(1) or m.group(2)))
    return None


class DeterministicProvider:
    name = "deterministic"
    model = None

    def complete(self, *, system: str, messages: list[dict], tools: list[dict], max_tokens: int, context: dict | None = None) -> ProviderTurn:
        context = context or {}
        available = {t["name"] for t in tools}
        idx, text = _last_user_text(messages)
        done = list(context["results"]) if context.get("results") is not None else _done_calls(messages, idx)
        intent: Intent = context.get("intent") or classify(text, context.get("conversation_context"))
        lang = context.get("language") or intent.language
        conv = context.get("conversation_context") or {}
        surface = context.get("surface", "customer")
        if surface == "insurer_copilot":
            return self._insurer(text, done, available, lang, conv)
        planner = getattr(self, f"_plan_{intent.name}", None) or self._plan_general
        nxt = planner(text, intent, done, available, conv, lang)
        if isinstance(nxt, ToolCall):
            return ProviderTurn(text=None, tool_calls=[nxt], stop_reason="tool_use")
        return ProviderTurn(text=nxt, stop_reason="end_turn")

    # ------------------------------------------------------------- plans ---

    def _plan_policy_question(self, text, intent, done, available, conv, lang):
        if not done and "search_policy_wording" in available:
            return _tc("search_policy_wording", query=text, top_k=6)
        res = _result(done, "search_policy_wording")
        return compose_policy_answer(text, res, lang)

    def _plan_summarize(self, text, intent, done, available, conv, lang):
        if not done:
            return _tc("get_insurance_portfolio")
        return compose_portfolio(_result(done, "get_insurance_portfolio"), lang)

    def _plan_gap_analysis(self, text, intent, done, available, conv, lang):
        if not done:
            return _tc("analyze_coverage_gaps")
        return compose_gaps(_result(done, "analyze_coverage_gaps"), lang)

    def _plan_claim_start(self, text, intent, done, available, conv, lang):
        inc = intent.incident_type or "other"
        if not done:
            return _tc("assess_incident_coverage", incident_type=inc, description=text)
        ranked = (_result(done, "assess_incident_coverage") or {}).get("ranked_policies") or []
        best = ranked[0] if ranked else None
        if best and best["verdict"] != "not_addressed" and not _result(done, "create_claim_draft") and "create_claim_draft" in available:
            args: dict[str, Any] = {"incident_type": inc, "description": text, "policy_id": best["policy_id"]}
            d = _parse_date(text)
            if d:
                args["incident_date"] = d.isoformat()
            money = _parse_money(text)
            if money:
                args["estimated_loss"] = money
            if re.search(r"\b(police|crime reference)\b|报警|报案|警察|公安", text, re.I):
                args["authorities_involved"] = True
            if re.search(r"\b(hurt|injured|bleeding|ambulance|danger)\b|受伤|流血|救护车|危险", text, re.I):
                args["safety_concern"] = True
            return _tc("create_claim_draft", **args)
        return compose_claim_started(text, inc, ranked, _result(done, "create_claim_draft"), lang)

    def _plan_claim_followup(self, text, intent, done, available, conv, lang):
        if not done:
            return _tc("list_claims")
        claims = (_result(done, "list_claims") or {}).get("claims") or []
        target = _pick_claim(claims, conv.get("claim_id"), prefer_open=True)
        if target and not _result(done, "get_claim_details"):
            return _tc("get_claim_details", claim_id=target["id"])
        if target and not _result(done, "get_claim_timeline"):
            return _tc("get_claim_timeline", claim_id=target["id"])
        return compose_followup(claims, _result(done, "get_claim_details"), _result(done, "get_claim_timeline"), lang)

    def _plan_draft_communication(self, text, intent, done, available, conv, lang):
        if not done:
            return _tc("list_claims")
        claims = (_result(done, "list_claims") or {}).get("claims") or []
        target = _pick_claim(claims, conv.get("claim_id"), prefer_open=True)
        kind = intent.comm_kind or "progress_update"
        if not _result(done, "create_communication_draft"):
            return _tc("create_communication_draft", kind=kind, claim_id=target["id"] if target else None, points=_extract_points(text))
        return compose_draft(_result(done, "create_communication_draft"), lang, target)

    def _plan_dispute(self, text, intent, done, available, conv, lang):
        if not done:
            return _tc("list_claims")
        claims = (_result(done, "list_claims") or {}).get("claims") or []
        decided = [c for c in claims if c["status"] in {"declined", "partially_approved"}]
        target = _pick_claim(decided or claims, conv.get("claim_id"), prefer_open=False)
        if not target:
            return compose_no_claims(lang)
        if not _result(done, "get_claim_decision"):
            return _tc("get_claim_decision", claim_id=target["id"])
        decision = _result(done, "get_claim_decision") or {}
        if decision.get("decision") and not _result(done, "create_communication_draft"):
            return _tc("create_communication_draft", kind=intent.comm_kind or "clarification_request", claim_id=target["id"], points=_extract_points(text))
        return compose_dispute(target, decision, _result(done, "create_communication_draft"), lang)

    def _plan_approve_send(self, text, intent, done, available, conv, lang):
        if conv.get("pending_communication_id") and not done:
            return _tc("send_communication", communication_id=conv["pending_communication_id"])
        if conv.get("claim_id") and not done and re.search(r"claim|submit|提交|理赔", text, re.I):
            return _tc("submit_claim", claim_id=conv["claim_id"])
        if not done:
            return _tc("list_claims")
        if _result(done, "send_communication") or _result(done, "submit_claim"):
            return compose_approval(_result(done, "send_communication") or _result(done, "submit_claim"), lang)
        claims = (_result(done, "list_claims") or {}).get("claims") or []
        ready = [c for c in claims if c["status"] in {"ready_for_review", "submission_failed", "awaiting_customer_approval"}]
        if ready and not _result(done, "submit_claim"):
            return _tc("submit_claim", claim_id=ready[0]["id"])
        return compose_nothing_to_send(claims, lang)

    def _plan_general(self, text, intent, done, available, conv, lang):
        return compose_help(lang)

    # ------------------------------------------------------------ insurer ---

    def _insurer(self, text, done, available, lang, conv):
        claim_id = conv.get("claim_id")
        if not claim_id:
            return ProviderTurn(text=("Open a claim in the workspace and I will summarise it." if lang != "zh" else "请先在工作台中打开一个理赔案件，我再为你总结。"))
        if not done:
            return ProviderTurn(text=None, tool_calls=[_tc("insurer_get_claim", claim_id=claim_id)], stop_reason="tool_use")
        claim = _result(done, "insurer_get_claim") or {}
        wants_draft = bool(re.search(r"\b(draft|request|ask the customer|write)\b|起草|请求|补充材料|写", text, re.I))
        missing = [i for i in (claim.get("platform_checks", {}).get("checklist") or []) if i["kind"] == "document" and i["status"] == "required" and not i["satisfied"]]
        if wants_draft and missing and not _result(done, "insurer_draft_information_request"):
            items = [i["label"].get(lang, i["label"]["en"]) for i in missing]
            refs = sorted({c["clause_ref"] for i in missing for c in i.get("citations", [])})
            return ProviderTurn(text=None, tool_calls=[_tc("insurer_draft_information_request", claim_id=claim_id, requested_items=items, clause_refs=refs)], stop_reason="tool_use")
        return ProviderTurn(text=compose_insurer_summary(claim, _result(done, "insurer_draft_information_request"), lang))


# ---------------------------------------------------------------- helpers ---


def _result(done: list[tuple[str, dict, dict]], name: str) -> dict | None:
    for n, _a, r in reversed(done):
        if n == name:
            return r
    return None


def _pick_claim(claims: list[dict], claim_id: str | None, *, prefer_open: bool) -> dict | None:
    if claim_id:
        for c in claims:
            if c["id"] == claim_id:
                return c
    if not claims:
        return None
    if prefer_open:
        open_ = [c for c in claims if c["status"] not in {"settled", "closed", "declined", "approved", "partially_approved"}]
        if open_:
            return open_[0]
    return claims[0]


def _extract_points(text: str) -> list[str]:
    quoted = re.findall(r"[\"“”']([^\"“”']{6,200})[\"“”']", text)
    return quoted[:4]


def _L(lang: str, en: str, zh: str) -> str:
    return zh if lang == "zh" else en


def _clause_line(hit: dict, lang: str, max_chars: int = 240) -> str:
    text = (hit.get("text") or hit.get("excerpt") or "").strip()
    tr = (hit.get("translations") or {}).get(lang)
    body = text if len(text) <= max_chars else text[:max_chars].rsplit(" ", 1)[0] + "…"
    line = f"- **[{hit['citation']}]** {hit.get('title', '')}: {body}"
    if tr and hit.get("language") != lang:
        tr_body = tr if len(tr) <= max_chars else tr[:max_chars] + "…"
        line += f"\n  - " + _L(lang, "Translation: ", "译文：") + tr_body
    return line


def _fmt_amount(v, cur="") -> str:
    try:
        return f"{float(v):,.0f} {cur}".strip()
    except (TypeError, ValueError):
        return str(v)


# ------------------------------------------------------------- composers ---


GEO_TAGS = {"usa", "canada", "caribbean", "europe", "worldwide", "geographic_scope", "hong_kong", "china", "excl_usa_canada"}
PERIL_TAGS = {
    "theft", "robbery", "loss", "fire", "storm", "flood", "escape_of_water", "water_damage", "accidental_damage", "collision", "own_damage",
    "vandalism", "screen_damage", "liquid_damage", "liability", "third_party", "windscreen", "medical", "illness", "injury", "personal_accident",
    "cancellation", "curtailment", "baggage", "delay", "missed_departure", "vet_fees", "death", "straying", "keys", "money",
    "delivery_use", "business_use", "pet_damage", "wear_and_tear", "pre_existing", "unattended", "alternative_accommodation", "subsidence",
}


def _topic(question: str) -> str:
    q = question.strip().rstrip("?？。.")
    q = re.sub(r"^(does|do|is|am|are|can|could|will|would|should)\s+(it|this|that|my [\w ]*?policy|my insurance|the policy|i|we)\s+(be\s+)?(cover(ed)?|insured|pay( for)?|claim( for)?)?\s*(for|in|if|when)?\s*", "", q, flags=re.I)
    q = re.sub(r"^(我的)?(保单|保险)?(保不保|赔不赔|能不能赔|有没有赔|算不算|能赔)?", "", q)
    return q.strip() or question.strip()


def compose_policy_answer(question: str, res: dict | None, lang: str) -> str:
    from ...services.retrieval import extract_tags

    res = res or {}
    hits = res.get("hits") or []
    searched = res.get("policies_searched") or []
    if res.get("silent") or not hits:
        pols = ", ".join(searched) if searched else _L(lang, "your policies", "你的保单")
        q = _topic(question)
        return _L(
            lang,
            f"I searched the wording of {pols} and did not find a clause that deals with this. I would rather tell you that than guess.\n\nWhat that means: the wording I hold does not address it, which is not the same as it being excluded. The insurer can confirm.\n\n**Next step:** I can draft a short question to your insurer: *\"Please confirm in writing whether my policy covers {q}, and under which clause.\"* Say \"draft that\" and I will prepare it for your approval.",
            f"我检索了 {pols} 的条款，没有找到涉及这个问题的内容。与其猜测，不如如实告诉你。\n\n这意味着：现有条款没有规定，但这不等于一定不保，需要保险公司确认。\n\n**下一步：**我可以帮你起草一条给保险公司的问题：“请书面确认我的保单是否承保{q}，依据哪一条款。”回复“起草”即可，发送前会先请你确认。",
        )
    from ...services.coverage import SUBJECT_TAGS, TYPE_SUBJECTS
    from ...services.retrieval import detect_insurance_types

    q_tags = extract_tags(question)
    perils = q_tags & PERIL_TAGS
    geo = q_tags & GEO_TAGS
    subjects = q_tags & SUBJECT_TAGS
    types = detect_insurance_types(question)
    top_score = hits[0].get("score") or 0
    hits = [h for h in hits if (h.get("score") or 0) >= 0.25 * top_score]

    def in_subject(h: dict) -> bool:
        if not subjects and not types:
            return True
        tags = set(h.get("tags") or [])
        if subjects and tags & subjects:
            return True
        if h.get("insurance_type") in types:
            return True
        if subjects and subjects & TYPE_SUBJECTS.get(h.get("insurance_type", ""), set()) and not types:
            return True
        return False

    def peril_match(h: dict) -> bool:
        tags = set(h.get("tags") or [])
        if not in_subject(h):
            return False
        return bool(tags & perils) if perils else bool(set(h.get("matched_tags") or []))

    cov_all = [h for h in hits if h["kind"] == "coverage"]
    not_selected = [h for h in cov_all if "not_selected" in (h.get("tags") or [])]
    cov_match = [h for h in cov_all if h not in not_selected and peril_match(h)]
    cov_other = [h for h in cov_all if h not in not_selected and h not in cov_match]
    exc_match = [h for h in hits if h["kind"] == "exclusion" and in_subject(h) and (bool(set(h.get("tags") or []) & perils) or not perils)]
    cond = [h for h in hits if h["kind"] in {"condition", "claims_procedure", "definition"}]
    limits = [h for h in hits if h["kind"] == "limit"]
    sep = _L(lang, "; ", "；")
    end = _L(lang, ".", "。")

    shown = cov_match[:3] + exc_match[:2] + (not_selected[:1] if not_selected else []) + cond[:2] + limits[:1] + cov_other[:1]
    seen, ordered = set(), []
    for h in shown:
        if h["citation"] not in seen:
            seen.add(h["citation"])
            ordered.append(h)
    lines = [_L(lang, "**What your wording states**", "**保单条款原文**")]
    lines.extend(_clause_line(h, lang) for h in ordered[:6])

    reading: list[str] = []
    if geo:
        for h in hits:
            t = set(h.get("tags") or [])
            if not (set(h.get("matched_tags") or []) & GEO_TAGS):
                continue
            if "excl_usa_canada" in t:
                reading.append(_L(lang, f"[{h['citation']}] limits the area of cover and **excludes** the USA and Canada (and the Caribbean)", f"[{h['citation']}] 限定了承保地域，**不包括**美国和加拿大（及加勒比地区）"))
            elif "usa" in t and "worldwide" in t:
                reading.append(_L(lang, f"[{h['citation']}] states cover is worldwide **including** the USA and Canada, subject to its conditions", f"[{h['citation']}] 载明承保范围为全球，**包括**美国和加拿大，以其条件为准"))
            elif t & {"geographic_scope", "europe", "china", "hong_kong"} and h["kind"] in {"condition", "coverage"}:
                reading.append(_L(lang, f"[{h['citation']}] sets the territorial limits for that policy", f"[{h['citation']}] 规定了该保单的地域范围"))
    if cov_match:
        reading.append(_L(lang, f"cover of this kind appears to be included under [{cov_match[0]['citation']}], subject to the policy's terms, limits and exclusions", f"[{cov_match[0]['citation']}] 项下看起来包含此类保障，以条款、限额和除外责任为准"))
    elif perils and not geo:
        reading.append(_L(lang, "I did not find a clause that covers this peril", "我没有找到承保这一风险的条款"))
    if not_selected:
        reading.append(_L(lang, f"[{not_selected[0]['citation']}] would be relevant but it is an optional extension your schedule shows as **not selected**", f"[{not_selected[0]['citation']}] 本应相关，但它是附表显示**未选择**的可选扩展"))
    if exc_match:
        if cov_match:
            reading.append(_L(lang, f"an exclusion may be relevant: [{exc_match[0]['citation']}] ({exc_match[0].get('title', '')}); whether it applies depends on the facts", f"可能相关的除外责任：[{exc_match[0]['citation']}]（{exc_match[0].get('title', '')}），是否适用取决于具体事实"))
        else:
            reading.append(_L(lang, f"[{exc_match[0]['citation']}] ({exc_match[0].get('title', '')}) appears to **exclude** this, so my reading is that it would not be paid under that policy", f"[{exc_match[0]['citation']}]（{exc_match[0].get('title', '')}）看起来将其列为**除外责任**，我的解读是该保单不会赔付"))
    amt = next((h for h in limits + cov_match if h.get("amount")), None)
    if amt and amt in cov_match + limits and amt.get("amount") and amt["amount"] > 0:
        reading.append(_L(lang, f"a figure that matters: [{amt['citation']}] states {_fmt_amount(amt['amount'])}", f"相关金额：[{amt['citation']}] 载明 {_fmt_amount(amt['amount'])}"))
    if cond and (perils or geo):
        reading.append(_L(lang, f"conditions to meet: [{cond[0]['citation']}] ({cond[0].get('title', '')})", f"需满足的条件：[{cond[0]['citation']}]（{cond[0].get('title', '')}）"))
    if not reading:
        reading.append(_L(lang, "the clauses above are the closest match; none gives a clean yes or no", "以上是最相关的条款，但没有一条能直接给出是或否"))
    lines.append("")
    lines.append(_L(lang, "**My reading** (not a decision; the insurer decides): ", "**我的解读**（不是理赔结论，由保险公司决定）：") + sep.join(reading) + end)
    lines.append("")
    lines.append(_L(lang, "**Next step:** if this is about something that has happened, tell me what occurred and I will check the claims conditions and start a claim pack. If you want certainty before relying on it, I can draft a written question to the insurer.", "**下一步：**如果是已经发生的事情，告诉我经过，我会核对理赔条件并开始准备理赔材料；如需在依赖前得到确认，我可以起草给保险公司的书面问题。"))
    return "\n".join(lines)


def compose_portfolio(res: dict | None, lang: str) -> str:
    pols = (res or {}).get("policies") or []
    if not pols:
        return _L(lang, "You have no policies recorded yet. Upload a policy document under My Insurance and I will read it.", "你还没有录入任何保单。可在“我的保险”中上传保单文件，我会读取它。")
    active = [p for p in pols if p["status"] == "active"]
    lines = [_L(lang, f"You have **{len(active)} active** polic{'y' if len(active) == 1 else 'ies'}" + (f" and {len(pols) - len(active)} expired" if len(pols) > len(active) else "") + ":", f"你有 **{len(active)} 份有效**保单" + (f"，另有 {len(pols) - len(active)} 份已到期" if len(pols) > len(active) else "") + "：")]
    for p in pols:
        prem = f"{_fmt_amount(p['premium_amount'], p['currency'])}/{p['premium_frequency']}" if p.get("premium_amount") is not None else "—"
        exc = _fmt_amount(p["excess_amount"], p["currency"]) if p.get("excess_amount") is not None else "—"
        status = {"active": _L(lang, "active", "有效"), "expired": _L(lang, "expired", "已到期")}.get(p["status"], p["status"])
        lines.append(f"- **{p['provider_name']} {p['product_name']}** ({p['policy_number']}, {insurance_label(p['insurance_type'], lang)}) · {status} · {_L(lang, 'ends', '到期')} {p['end_date']} ({p['days_to_end']} {_L(lang, 'days', '天')}) · {_L(lang, 'premium', '保费')} {prem} · {_L(lang, 'excess', '免赔额')} {exc}")
        summ = p.get("summary") or {}
        if summ.get("key_limits"):
            lines.append("  - " + _L(lang, "Key limits: ", "主要限额：") + "; ".join(summ["key_limits"][:3]))
        if summ.get("key_exclusions"):
            lines.append("  - " + _L(lang, "Watch out for: ", "注意除外：") + "; ".join(summ["key_exclusions"][:3]))
    soon = [p for p in active if 0 <= p["days_to_end"] <= 60]
    if soon:
        lines.append("")
        lines.append(_L(lang, "**Renewing soon:** ", "**即将到期：**") + ", ".join(f"{p['policy_number']} ({p['days_to_end']} {_L(lang, 'days', '天')})" for p in soon))
    lines.append("")
    lines.append(_L(lang, "Ask me \"find gaps in my cover\" for an analysis across all of them.", "可以问我“分析我的保障缺口”，我会跨所有保单进行分析。"))
    return "\n".join(lines)


def compose_gaps(res: dict | None, lang: str) -> str:
    res = res or {}
    findings = res.get("findings") or []
    counts = (res.get("summary") or {}).get("counts") or {}
    if not findings:
        return _L(lang, "I could not run the analysis: your profile has no assets or activities yet. Add them in Settings and ask again.", "无法进行分析：你的资料中还没有资产或活动信息。请在设置中补充后再试。")
    lines = [_L(lang, f"I checked {res['summary']['assets_considered']} declared assets and {res['summary']['activities_considered']} activities against {len(res['summary']['policies_considered'])} active policies. Result: **{counts.get('gap', 0)} gaps, {counts.get('partial', 0)} partial, {counts.get('duplicate', 0)} duplicate, {counts.get('covered', 0)} covered**.", f"我将你申报的 {res['summary']['assets_considered']} 项资产和 {res['summary']['activities_considered']} 项活动与 {len(res['summary']['policies_considered'])} 份有效保单进行了对照。结果：**缺口 {counts.get('gap', 0)} 项，部分保障 {counts.get('partial', 0)} 项，重复 {counts.get('duplicate', 0)} 项，已保障 {counts.get('covered', 0)} 项**。")]
    icons = {"gap": "🔴", "partial": "🟠", "duplicate": "🟣", "note": "ℹ️", "covered": "🟢"}
    shown = [f for f in findings if f["status"] in {"gap", "partial", "duplicate"}][:6]
    for f in shown:
        ev = " ".join(f"[{e['citation']}]" for e in f.get("evidence", [])[:2])
        lines.append(f"\n{icons.get(f['status'], '•')} **{f['label'].get(lang, f['label']['en'])}** — {f['explanation'].get(lang, f['explanation']['en'])} {ev}\n  - {_L(lang, 'Suggested: ', '建议：')}{f['suggested_action'].get(lang, f['suggested_action']['en'])}")
    covered = [f for f in findings if f["status"] == "covered"]
    if covered:
        lines.append("\n" + _L(lang, "**Looks fine:** ", "**看起来没问题：**") + ", ".join(f"{f['label'].get(lang, f['label']['en'])} [{f['evidence'][0]['citation']}]" if f.get("evidence") else f["label"].get(lang, f["label"]["en"]) for f in covered[:5]))
    lines.append("\n" + (res.get("disclaimer") or {}).get(lang, ""))
    return "\n".join(lines)


def compose_claim_started(text: str, inc: str, ranked: list[dict], created: dict | None, lang: str) -> str:
    if created and created.get("error"):
        detail = created.get("error")
        if created.get("details"):
            detail += ": " + "; ".join(f"{'.'.join(str(x) for x in d.get('loc', []))} {d.get('msg')}" for d in created["details"])
        return _L(lang, f"I could not open the claim draft ({detail}). Nothing was recorded. Tell me the missing detail and I will try again, or start the claim from the Claims page.", f"无法建立理赔草稿（{detail}）。没有记录任何内容。请补充缺少的信息后再试，或在“理赔”页面手动发起。")
    if not ranked or ranked[0]["verdict"] == "not_addressed" or not created:
        best = ranked[0] if ranked else None
        return _L(
            lang,
            f"I'm sorry this happened. I read your policies for a **{incident_label(inc, 'en').lower()}** incident and none of them appears to address it" + (f" (closest: {best['policy_name']}, {best['policy_number']})" if best else "") + ". That is a reading of the wording, not a decision; if you think a policy should respond, tell me which one and I will open a claim draft against it and we can put the question to the insurer.",
            f"很遗憾发生了这样的事。我按**{incident_label(inc, 'zh')}**类型查看了你的保单，没有一份看起来涉及这类事件" + (f"（最接近的是 {best['policy_name']}，{best['policy_number']}）" if best else "") + "。这是对条款的解读而非结论；如果你认为某份保单应当承保，告诉我是哪一份，我会据此建立理赔草稿并向保险公司提出。",
        )
    claim = created.get("claim") or {}
    cv = claim.get("coverage_view") or {}
    summary = (cv.get("summary") or {}).get(lang) or (cv.get("summary") or {}).get("en", "")
    req = [i for i in claim.get("checklist", []) if i["status"] == "required" and not i["satisfied"]]
    rec = [i for i in claim.get("checklist", []) if i["status"] == "recommended" and not i["satisfied"]]
    lines = []
    if claim.get("incident", {}).get("safety_concern"):
        lines.append(_L(lang, "**If anyone is hurt or in danger, call the emergency services first. The claim can wait.**\n", "**如有人受伤或处于危险中，请先拨打急救或报警电话，理赔可以稍后处理。**\n"))
    lines.append(_L(lang, f"I'm sorry about this. I have opened claim **{claim['reference']}** against **{claim.get('policy_name')}** ({claim.get('policy_number')}) with what you told me; nothing has been sent to the insurer.", f"很遗憾发生这样的事。我已根据你的描述在 **{claim.get('policy_name')}**（{claim.get('policy_number')}）下建立了理赔 **{claim['reference']}**，尚未发送给保险公司。"))
    lines.append("")
    lines.append(_L(lang, "**Coverage reading:** ", "**保障解读：**") + summary)
    conds = cv.get("conditions") or []
    if conds:
        lines.append(_L(lang, "**Conditions to watch:** ", "**需注意的条件：**") + "; ".join(f"[{c['citation']}] {c['title']}" for c in conds[:3]))
    lines.append("")
    if req:
        lines.append(_L(lang, "**Still needed before it can be submitted:**", "**提交前还需要：**"))
        for i in req[:6]:
            cite = f" [{i['citations'][0]['citation']}]" if i.get("citations") else ""
            lines.append(f"- {i['label'].get(lang, i['label']['en'])}{cite} — {i['reason'].get(lang, i['reason']['en'])}")
    if rec:
        lines.append(_L(lang, "**Recommended:** ", "**建议提供：**") + ", ".join(i["label"].get(lang, i["label"]["en"]) for i in rec[:4]))
    lines.append("")
    lines.append(_L(lang, f"Progress: {claim.get('completeness', {}).get('progress', 0)}%. Upload documents on the claim page; when the pack is complete, say \"submit the claim\" and I will prepare it for your approval.", f"当前进度：{claim.get('completeness', {}).get('progress', 0)}%。请在理赔页面上传材料；材料齐全后，回复“提交理赔”，我会准备好供你确认。"))
    return "\n".join(lines)


def compose_followup(claims: list[dict], details: dict | None, timeline: dict | None, lang: str) -> str:
    if not claims:
        return compose_no_claims(lang)
    c = (details or {}).get("claim")
    if not c:
        lines = [_L(lang, "Your claims:", "你的理赔：")]
        for cl in claims[:6]:
            lines.append(f"- **{cl['reference']}** · {cl['status_label']} · {_L(lang, 'last update', '最近更新')} {cl['status_updated_at'][:10]}")
        return "\n".join(lines)
    label = STATUS_LABELS.get(c["status"], {}).get(lang, c["status"])
    lines = [_L(lang, f"**{c['reference']}** ({c.get('policy_name')}): last known status **{label}**, recorded by {c['status_source']} at {c['status_updated_at'][:16].replace('T', ' ')}.", f"**{c['reference']}**（{c.get('policy_name')}）：最近已知状态为 **{label}**，来源 {c['status_source']}，时间 {c['status_updated_at'][:16].replace('T', ' ')}。")]
    if c.get("insurer_reference"):
        lines.append(_L(lang, f"Insurer reference: {c['insurer_reference']}.", f"保险公司受理编号：{c['insurer_reference']}。"))
    reqs = c.get("open_information_requests") or []
    if reqs:
        r = reqs[0]
        lines.append("")
        lines.append(_L(lang, "**The insurer is waiting on you:** ", "**保险公司正在等你补充：**") + r["message"])
        if r.get("items"):
            lines.extend(f"- {i}" for i in r["items"])
        if r.get("due_date"):
            lines.append(_L(lang, f"Requested by {r['due_date']}.", f"要求在 {r['due_date']} 前提供。"))
        lines.append(_L(lang, "Upload the items on the claim page and I will help you send the response.", "请在理赔页面上传相应材料，我会协助你发送回复。"))
    elif c["status"] in {"draft", "information_required", "ready_for_review"}:
        comp = c.get("completeness") or {}
        missing = comp.get("missing_required_documents", []) + comp.get("missing_fields", [])
        lines.append("")
        lines.append(_L(lang, f"It has not been submitted yet (progress {comp.get('progress', 0)}%)." + (f" Still missing: {', '.join(missing)}." if missing else " It is ready; say \"submit the claim\" to prepare it for approval."), f"尚未提交（进度 {comp.get('progress', 0)}%）。" + (f"仍缺：{'、'.join(missing)}。" if missing else "已就绪，回复“提交理赔”即可准备确认。")))
    elif c.get("decision"):
        d = c["decision"]
        lines.append("")
        lines.append(_L(lang, f"**Decision:** {d['outcome'].replace('_', ' ')} — {d['reason']}", f"**结论：**{d['outcome']} — {d['reason']}"))
        lines.append(_L(lang, "Say \"explain the decision\" and I will go through the clauses relied on and what could be clarified.", "回复“解释这个结论”，我会逐条说明所依据的条款以及可以澄清的地方。"))
    tl = (timeline or {}).get("timeline") or []
    if tl:
        lines.append("")
        lines.append(_L(lang, "**Recent activity:**", "**最近动态：**"))
        for e in tl[-4:]:
            lines.append(f"- {e['at'][:10]} · {e['summary']}")
    return "\n".join(lines)


def compose_draft(res: dict | None, lang: str, claim: dict | None) -> str:
    comm = (res or {}).get("communication")
    if not comm:
        return _L(lang, "I could not prepare a draft: " + str((res or {}).get("error", "unknown error")), "无法起草：" + str((res or {}).get("error", "未知错误")))
    return _L(
        lang,
        f"Here is a draft{(' for claim ' + claim['reference']) if claim else ''}. Facts come from your records; anything in [square brackets] is for you to fill in because I do not have it.\n\n**Subject:** {comm['subject']}\n\n> " + comm["body"].replace("\n", "\n> ") + "\n\nEdit it if you like. When you are happy, say \"send it\" and I will prepare an approval card; nothing goes to the insurer until you approve.",
        f"以下是草稿{('（理赔 ' + claim['reference'] + '）') if claim else ''}。事实均来自你的记录；[方括号] 中的内容是我没有的信息，请你补充。\n\n**主题：**{comm['subject']}\n\n> " + comm["body"].replace("\n", "\n> ") + "\n\n可以修改。满意后回复“发送”，我会生成确认卡片；在你确认之前不会发给保险公司。",
    )


def compose_dispute(target: dict, decision_res: dict, draft_res: dict | None, lang: str) -> str:
    d = decision_res.get("decision")
    if not d:
        return _L(lang, f"Claim {target['reference']} has no recorded decision yet, so there is nothing to challenge. Its last known status is {target['status_label']}.", f"理赔 {target['reference']} 尚未记录结论，暂时没有可以申诉的内容。最近已知状态：{target['status_label']}。")
    relied = decision_res.get("clauses_relied_on") or []
    routes = decision_res.get("review_route_clauses") or []
    lines = [_L(lang, f"**What the insurer decided on {target['reference']}:** {d['outcome'].replace('_', ' ')} on {d['decided_at'][:10]}" + (f" by {d['decided_by']}" if d.get("decided_by") else "") + ".", f"**保险公司对 {target['reference']} 的结论：**{d['outcome']}（{d['decided_at'][:10]}" + (f"，经办人 {d['decided_by']}" if d.get("decided_by") else "") + "）。")]
    lines.append(_L(lang, "**Their stated reason:** ", "**所述理由：**") + d["reason"])
    if relied:
        lines.append("")
        lines.append(_L(lang, "**Clauses they relied on, in the wording:**", "**所依据的条款原文：**"))
        for c in relied[:3]:
            lines.append(_clause_line(c, lang, 300))
    lines.append("")
    lines.append(_L(lang, "**What the decision turns on (my reading):** the reason rests on facts the insurer says it found. A review usually succeeds only if those facts are wrong or incomplete, or if the clause does not say what they say it says. I cannot tell you it will succeed.", "**结论的关键（我的解读）：**理由建立在保险公司认定的事实之上。通常只有在这些事实有误或不完整，或条款并不支持其结论时，复核才可能成功。我无法保证结果。"))
    lines.append(_L(lang, "**Worth clarifying or evidencing:** (1) exactly which facts and dates the insurer relied on; (2) any record that contradicts or qualifies them; (3) the specific clause wording versus what happened.", "**值得澄清或举证的：**（1）保险公司具体依据了哪些事实和日期；（2）任何与之矛盾或可补充说明的记录；（3）条款原文与实际情况的对照。"))
    if routes:
        lines.append(_L(lang, "**Review route in your wording:** ", "**条款中的复核途径：**") + "; ".join(f"[{r['citation']}] {r['title']}" for r in routes[:2]))
    comm = (draft_res or {}).get("communication")
    if comm:
        lines.append("")
        lines.append(_L(lang, f"I have prepared a **{comm['kind'].replace('_', ' ')}** draft ({comm['subject']}). It asks for the reasons and evidence in writing and leaves [placeholders] for anything I do not know. Review it in the card; say \"send it\" when ready and you will get an approval step first.", f"我已准备了一份**{comm['kind']}**草稿（{comm['subject']}），要求保险公司书面说明理由和证据，未知信息留有 [占位符]。请在卡片中查看；准备好后回复“发送”，发送前会有确认步骤。"))
    lines.append("")
    lines.append(_L(lang, "If the sums involved are significant or a legal point is in play, a regulated adviser or an ombudsman service may be the right next step; your wording's complaints clause describes the route.", "如金额较大或涉及法律问题，可考虑咨询持牌顾问或申诉渠道；保单的投诉条款描述了具体途径。"))
    return "\n".join(lines)


def compose_approval(res: dict | None, lang: str) -> str:
    res = res or {}
    if res.get("status") != "approval_required":
        return _L(lang, "I can't prepare that yet: " + str(res.get("error", "unknown error")).rstrip(".") + ". Nothing was sent." + (" Next: " + res["safe_next_step"] + "." if res.get("safe_next_step") else ""), "暂时无法准备该操作：" + str(res.get("error", "未知错误")) + "。尚未发送任何内容。" + ("下一步：" + res["safe_next_step"] + "。" if res.get("safe_next_step") else ""))
    cons = "\n".join(f"- {c.get(lang, c.get('en'))}" for c in res.get("consequences", []))
    return _L(
        lang,
        f"Ready for your approval. **Nothing has been sent yet.**\n\n**Action:** {res['summary']}\n\n**What happens if you approve:**\n{cons}\n\nUse the approval card to approve or reject. The card expires at {res['expires_at'][:16].replace('T', ' ')} UTC.",
        f"已准备好，等待你的批准。**目前尚未发送任何内容。**\n\n**操作：**{res['summary']}\n\n**批准后会发生什么：**\n{cons}\n\n请在确认卡片中批准或拒绝。卡片将于 {res['expires_at'][:16].replace('T', ' ')} UTC 过期。",
    )


def compose_nothing_to_send(claims: list[dict], lang: str) -> str:
    return _L(lang, "There is nothing ready to send: no draft message is pending and no claim is ready for submission. Tell me what you would like to send, or open the claim you mean.", "目前没有可发送的内容：没有待发送的草稿，也没有准备好提交的理赔。告诉我你想发送什么，或打开对应的理赔。")


def compose_no_claims(lang: str) -> str:
    return _L(lang, "You have no claims yet. If something has happened, tell me what occurred and I will check your cover and start a claim pack.", "你还没有任何理赔。如果发生了什么事，告诉我经过，我会核对保障并开始准备理赔材料。")


def compose_help(lang: str) -> str:
    return _L(
        lang,
        "I can help with three things, always from your own policy wording and with clause citations:\n\n- **Policy questions** — \"Does my home policy cover accidental damage?\" \"Am I covered in the USA?\"\n- **Coverage gaps** — \"Find gaps and overlaps in my insurance.\"\n- **Claims** — \"My bike was stolen from outside the office yesterday.\" I will read the claims conditions, build the checklist and prepare the pack. You approve anything that is sent.\n\nI can also explain a claim decision and draft messages to your insurer. What would you like to do?",
        "我可以帮你做三件事，全部基于你自己的保单条款并附条款引用：\n\n- **保单问题** — “我的家财险保不保意外损坏？”“去美国有没有保障？”\n- **保障缺口** — “分析我的保险缺口和重复。”\n- **理赔** — “我的电动车昨晚在楼下被偷了。”我会核对理赔条件、生成材料清单并准备材料包，所有发送都需你确认。\n\n我也可以解释理赔结论、起草给保险公司的信件。你想做什么？",
    )


def compose_insurer_summary(claim: dict, draft: dict | None, lang: str) -> str:
    if not claim or not claim.get("claim"):
        return _L(lang, "I could not load that claim.", "无法加载该理赔。")
    c = claim["claim"]
    st = claim.get("customer_statement") or {}
    checks = claim.get("platform_checks") or {}
    comp = checks.get("completeness") or {}
    docs = claim.get("documents") or []
    flagged = [d for d in docs if d.get("injection_flags")]
    missing = [i for i in (checks.get("checklist") or []) if i["status"] == "required" and not i["satisfied"]]
    lines = [_L(lang, f"**Case summary — {c['reference']}** (status {c['status']}, {c.get('priority')} priority)", f"**案件摘要 — {c['reference']}**（状态 {c['status']}，优先级 {c.get('priority')}）")]
    lines.append(_L(lang, f"- [customer statement] {st.get('incident_type', '').replace('_', ' ')} on {st.get('incident_date')} at {st.get('location')}: {st.get('description', '')[:300]}", f"- [客户陈述] {st.get('incident_date')} 在 {st.get('location')} 发生 {st.get('incident_type', '')}：{st.get('description', '')[:300]}"))
    if st.get("estimated_loss") is not None:
        lines.append(_L(lang, f"- [customer statement] estimated loss {st['estimated_loss']:,.0f}; items: " + ", ".join(f"{i.get('label')} ({i.get('value')})" for i in (st.get("items_involved") or [])[:5]), f"- [客户陈述] 预计损失 {st['estimated_loss']:,.0f}；物品：" + "，".join(f"{i.get('label')}（{i.get('value')}）" for i in (st.get("items_involved") or [])[:5])))
    for d in docs[:6]:
        lines.append(_L(lang, f"- [document: {d['filename']}] type {d['doc_type']}, extraction {d['extraction_status']}" + (f", excerpt: {d['extracted_excerpt'][:120]}…" if d.get("extracted_excerpt") else ""), f"- [文件：{d['filename']}] 类型 {d['doc_type']}，提取状态 {d['extraction_status']}" + (f"，摘录：{d['extracted_excerpt'][:120]}…" if d.get("extracted_excerpt") else "")))
    lines.append(_L(lang, f"- [platform check] completeness {comp.get('progress', 0)}%, " + (f"missing required: {', '.join(i['key'] for i in missing)}" if missing else "all required items present") + (f"; conflicts: {', '.join(x['code'] for x in comp.get('conflicts', []))}" if comp.get("conflicts") else ""), f"- [平台校验] 完整度 {comp.get('progress', 0)}%，" + (f"缺少必需项：{'、'.join(i['key'] for i in missing)}" if missing else "必需项齐全") + (f"；冲突：{'、'.join(x['code'] for x in comp.get('conflicts', []))}" if comp.get("conflicts") else "")))
    cr = checks.get("coverage_reading") or {}
    if cr.get("covering_clauses") or cr.get("possible_exclusions"):
        lines.append(_L(lang, "- [platform check] customer side reading: " + ", ".join(f"[{x['citation']}]" for x in cr.get("covering_clauses", [])[:3]) + (" | possible exclusions " + ", ".join(f"[{x['citation']}]" for x in cr.get("possible_exclusions", [])[:2]) if cr.get("possible_exclusions") else ""), "- [平台校验] 客户侧条款解读：" + "、".join(f"[{x['citation']}]" for x in cr.get("covering_clauses", [])[:3]) + ("｜可能除外 " + "、".join(f"[{x['citation']}]" for x in cr.get("possible_exclusions", [])[:2]) if cr.get("possible_exclusions") else "")))
    if flagged:
        lines.append(_L(lang, f"- ⚠ [platform check] {len(flagged)} document(s) contain instruction like text ({', '.join(d['filename'] for d in flagged)}); I ignored it. Worth a look.", f"- ⚠ [平台校验] {len(flagged)} 份文件含有类似指令的文字（{'、'.join(d['filename'] for d in flagged)}），我已忽略，建议人工查看。"))
    lines.append("")
    lines.append(_L(lang, "**Points for human assessment** [my interpretation]: ", "**需人工评估的要点** [我的解读]：") + _L(lang, "verify the incident date against the policy period, check the stated cause against the exclusions above, and confirm proof of value for the larger items. I do not assess liability or quantum.", "核对事件日期与保险期间，对照上述除外条款核实陈述的原因，确认大额物品的价值证明。我不评估责任或赔付金额。"))
    if draft and draft.get("draft"):
        lines.append("")
        lines.append(_L(lang, "I drafted an information request (shown in the card). Review and send it from the workspace.", "我已起草补充材料请求（见卡片），请在工作台中审阅并发送。"))
    return "\n".join(lines)
