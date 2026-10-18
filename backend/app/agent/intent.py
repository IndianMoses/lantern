"""Deterministic intent classification (English and 中文).

Used for planning, logging and the offline provider. With the Claude API
the model still chooses its own tools; this classification labels the run
and seeds the plan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..services.retrieval import detect_language, extract_tags
from ..services.taxonomy import INCIDENT_TYPES

PATTERNS: dict[str, list[str]] = {
    "approve_send": [r"\b(yes[, ]+)?(send|submit|go ahead|approve|confirm)\b.*\b(it|claim|email|message|letter|now|please)?\b", r"^(send it|submit it|yes send|yes submit|go ahead|confirm|approve)[.!]?$", r"(发送|提交|发出去|确认发送|就这样发|可以发了|提交吧|发吧)"],
    "dispute": [r"\b(declined|rejected|refused|turned down|reconsider|appeal|review the decision|dispute|challenge|partly approved|partially approved|only (partly|partially)|ask for a review|request a review|review of (the|my|this) (decision|claim)|explain (the|my|this|why) .*decision|why .*(declined|rejected|approved|refused))\b", r"(拒赔|被拒|不赔.*怎么办|复核|复议|申诉|不服|理由|部分赔付|只赔|为什么.*(拒|不赔|只赔))"],
    "draft_communication": [r"\b(draft|write|compose|prepare)\b.*\b(email|letter|message|note|reply|response|complaint|question)\b", r"^\s*(draft|write|prepare) (that|it|one|this)\b", r"^\s*(起草|写)(吧|一下|那个|这个)?\s*$", r"\b(chase|follow up|ask (them|the insurer|my insurer) for (an )?update)\b", r"(写|起草|拟|发)(一封|个|份)?(邮件|信|函|消息|投诉)", r"(催|问进度|询问进度)"],
    "claim_followup": [r"\b(status|progress|update|where is|what('s| is) happening|how long)\b.*\b(claim|my claim)\b", r"\bmy claim\b.*\b(status|update|progress)\b", r"\b(what|which) (documents?|evidence|information|else)\b.*\b(need|still|missing|provide|outstanding)\b", r"\b(still (need|missing|outstanding)|what('s| is) (missing|outstanding|left))\b", r"(理赔|索赔)(进度|状态|到哪|怎么样了|进展)", r"(进度|进展)", r"(还需要|还缺|缺什么|还要提供)(什么|哪些)?(材料|文件|证据)?"],
    "gap_analysis": [r"\b(gap|gaps|overlap|overlapping|duplicate|duplicated|double cover|paying twice|missing cover|am i (fully|properly|adequately) covered|what am i not covered for|coverage review|insurance review|audit)\b", r"(缺口|漏保|重复|重叠|够不够|有没有漏|保障分析|全不全)"],
    "claim_start": [r"\b(file|make|start|open|submit|raise|begin|lodge|report|put in)\b.{0,25}\bclaim\b", r"\b(stolen|stole|theft|robbed|burgl|broke into|crash|accident|damaged|flooded|leak|leaking|burst|fire|lost my|hit my|dropped|cracked|smashed|hail|storm damage|injured|vet)\b", r"(理赔|索赔|报案|被盗|被偷|丢了|撞|事故|摔|碎|进水|漏水|着火|起火|受伤|淹|砸)"],
    "summarize": [r"\b(summari[sz]e|summary|overview|list|show)\b.*\b(polic(y|ies)|cover|insurance|portfolio)\b", r"\bwhat (insurance|policies|cover) do i have\b", r"(总结|汇总|概览|列出|我有哪些)(我的)?(保单|保险|保障)"],
    "policy_question": [r"\b(cover|covered|coverage|exclu|excess|deductible|limit|clause|wording|policy|insur|claimable|pay out|payout|eligible)\b", r"(保不保|赔不赔|能不能赔|有没有赔|保吗|赔吗|保障|免赔|限额|条款|除外|不保|不赔|保单|保险|理赔范围|算不算)"],
}
_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in PATTERNS.items()}

COMM_KIND_PATTERNS = [
    ("reconsideration_request", r"(reconsider|appeal|review|overturn|challenge|contest|dispute|复核|复议|申诉|不服)"),
    ("clarification_request", r"(explain|why|reason|clarif|说明|为什么|理由)"),
    ("complaint_delay", r"(complain|complaint|delay|slow|too long|投诉|拖|太慢|延误)"),
    ("progress_update", r"(update|progress|status|chase|follow up|进度|催|进展)"),
    ("missing_evidence", r"(evidence|documents?|receipt|photo|attach|材料|证据|发票|照片|补充)"),
    ("information_response", r"(respond|reply|answer|回复|答复)"),
    ("coverage_question", r"(ask|question|confirm|whether|that|it|one|this|问|确认|是否|起草|写)"),
]

STRONG_INCIDENT = re.compile(r"\b(my|our) [\w' ]{0,30}?(was|were|got|has been|have been|had been) (stolen|damaged|lost|broken|hit|flooded|burnt|burned|crashed|smashed|cracked|vandali[sz]ed|taken)\b|\bi (crashed|dropped|lost|smashed|cracked|broke)\b|\b(someone|somebody) (stole|took|hit|broke into)\b|我的[\w ]{0,12}(被偷|被盗|丢了|摔了|撞了|坏了|进水了|碎了|被撞|着火了|烧了)", re.I)

QUESTION_HINTS = re.compile(r"\?|？|^\s*(does|do|is|am|are|can|could|would|will|what|which|how|when|where|why|should)\b|(吗|呢|怎么|如何|什么|哪些|是否|多少|算不算|保不保|赔不赔)", re.I)


@dataclass
class Intent:
    name: str
    language: str
    confidence: float
    incident_type: str | None = None
    comm_kind: str | None = None
    tags: set[str] = field(default_factory=set)
    is_question: bool = False


def guess_incident_type(text: str, tags: set[str]) -> str | None:
    priority = ["screen_damage", "liquid_damage", "escape_of_water", "flood", "fire", "theft", "collision", "windscreen", "vandalism", "storm", "third_party_liability", "medical", "cancellation", "baggage", "delay", "rider_injury", "injury", "illness", "accidental_damage", "loss"]
    tag_map = {
        "screen_damage": {"screen_damage"}, "liquid_damage": {"liquid_damage"}, "escape_of_water": {"escape_of_water"}, "flood": {"flood"}, "fire": {"fire"}, "theft": {"theft", "robbery"},
        "collision": {"collision"}, "windscreen": {"windscreen"}, "vandalism": {"vandalism"}, "storm": {"storm"}, "third_party_liability": {"third_party", "liability"}, "medical": {"medical"},
        "cancellation": {"cancellation", "curtailment"}, "baggage": {"baggage"}, "delay": {"delay", "missed_departure"}, "rider_injury": {"personal_accident"}, "injury": {"injury"}, "illness": {"illness"},
        "accidental_damage": {"accidental_damage"}, "loss": {"loss"},
    }
    low = text.lower()
    if any(k in low for k in ("pet", "dog", "cat", "vet", "宠物", "狗", "猫")) and tags & {"illness", "medical", "vet_fees", "injury"}:
        return "injury" if "injury" in tags and "illness" not in tags else "illness"
    if tags & {"ebike", "rider"} and tags & {"injury", "medical", "personal_accident"} and not tags & {"third_party", "collision"}:
        return "rider_injury"
    if tags & {"medical", "illness"} and tags & {"travel", "geographic_scope"}:
        return "medical"
    for it in priority:
        if tag_map[it] & tags:
            if it == "third_party_liability" and tags & {"collision"}:
                return "collision"
            return it
    return None


def classify(text: str, context: dict | None = None) -> Intent:
    lang = detect_language(text)
    tags = extract_tags(text)
    low = text.strip().lower()
    is_q = bool(QUESTION_HINTS.search(text))
    context = context or {}
    scores: dict[str, float] = {}
    for name, regs in _COMPILED.items():
        hits = sum(1 for r in regs if r.search(low))
        if hits:
            scores[name] = hits
    # ordering rules
    if "approve_send" in scores and (context.get("pending_communication_id") or context.get("claim_id") or re.search(r"\b(send|submit)\b|发送|提交", low)) and len(low) < 60:
        name = "approve_send"
    elif "dispute" in scores:
        name = "dispute"
    elif "draft_communication" in scores:
        name = "draft_communication"
    elif "gap_analysis" in scores:
        name = "gap_analysis"
    elif "claim_followup" in scores:
        name = "claim_followup"
    elif "summarize" in scores:
        name = "summarize"
    elif "claim_start" in scores and (not is_q or STRONG_INCIDENT.search(low)):
        name = "claim_start"
    elif "claim_start" in scores and re.search(r"\b(file|make|start|open|submit|raise|lodge|report)\b.*\bclaim\b|想(理赔|索赔|报案)|怎么(理赔|索赔|报案)|我要(理赔|索赔|报案)", low):
        name = "claim_start"
    elif "policy_question" in scores or (is_q and tags):
        name = "policy_question"
    elif "claim_start" in scores:
        name = "claim_start"
    else:
        name = "general"
    comm_kind = None
    if name in {"draft_communication", "dispute"}:
        for kind, pat in COMM_KIND_PATTERNS:
            if re.search(pat, low):
                comm_kind = kind
                break
        if name == "dispute":
            comm_kind = comm_kind if comm_kind in {"reconsideration_request", "clarification_request"} else "clarification_request"
        comm_kind = comm_kind or "progress_update"
    confidence = min(0.95, 0.5 + 0.15 * scores.get(name, 0)) if name != "general" else 0.3
    return Intent(name=name, language=lang, confidence=confidence, incident_type=guess_incident_type(text, tags), comm_kind=comm_kind, tags=tags, is_question=is_q)
