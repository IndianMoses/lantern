"""Bilingual (English / 中文) lexical retrieval over policy clauses.

Design choices
--------------
* Pure Python BM25 over clause text, titles, section names and tags. No
  embedding service is needed, which keeps the demo fully offline and the
  ranking explainable: every hit can show which terms matched.
* English is stemmed lightly and expanded through a synonym map; Chinese is
  indexed as character unigrams + bigrams. A shared bilingual lexicon maps
  words in either language to the canonical tag vocabulary used across the
  gap engine, checklist rules and coverage view, so a Chinese question can
  find an English clause and vice versa.
* Retrieval is always scoped by the caller to the policies the principal
  is authorised to read; the index is built per request from those rows.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

_CJK = re.compile(r"[一-鿿]+")
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")

STOPWORDS_EN = set(
    """a an and are as at be by for from has have i if in into is it its my of on or our that the their there these this to was we were what when where which while who will with you your yours do does did am can could would should about any also been being both but each few more most other some such than then too very just over under up out no nor not only own same so s t me he she they them his her him""".split()
)

# word or phrase -> tags (canonical vocabulary shared across services)
TAG_LEXICON: dict[str, tuple[str, ...]] = {
    # --- English
    "bike": ("bicycle",), "bicycle": ("bicycle",), "cycle": ("bicycle",), "pedal cycle": ("bicycle",), "road bike": ("bicycle",),
    "e-bike": ("ebike",), "ebike": ("ebike",), "electric bike": ("ebike",), "electric bicycle": ("ebike",), "scooter": ("ebike",), "moped": ("ebike",),
    "phone": ("phone",), "mobile": ("phone",), "smartphone": ("phone",), "handset": ("phone",), "iphone": ("phone",), "screen": ("screen_damage",), "cracked screen": ("screen_damage",),
    "laptop": ("laptop",), "computer": ("laptop", "computer"), "tablet": ("tablet",), "camera": ("valuables",), "gadget": ("gadget",),
    "stolen": ("theft",), "steal": ("theft",), "stole": ("theft",), "theft": ("theft",), "burglary": ("theft",), "burgled": ("theft",), "robbed": ("theft",), "robbery": ("theft",), "break in": ("theft", "forcible_entry"), "broke in": ("theft", "forcible_entry"),
    "lost": ("loss",), "lose": ("loss",), "loss": ("loss",), "missing": ("loss",),
    "leak": ("escape_of_water", "water_damage"), "leaking": ("escape_of_water", "water_damage"), "burst pipe": ("escape_of_water", "water_damage"), "pipe": ("escape_of_water",), "water damage": ("water_damage",), "flooded": ("flood", "water_damage"), "flood": ("flood",), "flooding": ("flood",),
    "storm": ("storm",), "wind": ("storm",), "gale": ("storm",), "hail": ("storm",), "lightning": ("fire",), "fire": ("fire",), "burnt": ("fire",), "burned": ("fire",), "smoke": ("fire",),
    "crash": ("collision", "accident"), "crashed": ("collision", "accident"), "collision": ("collision",), "accident": ("accident",), "hit": ("collision",), "rear ended": ("collision",), "bump": ("collision",), "scratched the car": ("collision",),
    "windscreen": ("windscreen",), "windshield": ("windscreen",), "glass": ("windscreen",),
    "cancel": ("cancellation",), "cancelled": ("cancellation",), "cancellation": ("cancellation",), "cut short": ("curtailment",), "curtail": ("curtailment",),
    "delay": ("delay",), "delayed": ("delay",), "missed flight": ("missed_departure",), "missed departure": ("missed_departure",),
    "baggage": ("baggage",), "luggage": ("baggage",), "suitcase": ("baggage",),
    "doctor": ("medical",), "hospital": ("medical",), "medical": ("medical",), "ill": ("illness", "medical"), "illness": ("illness", "medical"), "sick": ("illness", "medical"), "injured": ("injury", "medical"), "injury": ("injury", "medical"), "treatment": ("medical",),
    "excess": ("excess",), "deductible": ("excess",), "limit": ("limit",), "maximum": ("limit",), "sub limit": ("limit",), "single item": ("single_item_limit",),
    "usa": ("usa", "excl_usa_canada"), "us": ("usa",), "america": ("usa",), "united states": ("usa",), "new york": ("usa",), "florida": ("usa",), "canada": ("canada", "excl_usa_canada"), "caribbean": ("caribbean",), "europe": ("europe",), "abroad": ("geographic_scope",), "worldwide": ("worldwide",),
    "dog": ("pet", "dog"), "cat": ("pet",), "pet": ("pet",), "puppy": ("pet", "dog"), "vet": ("vet_fees",), "veterinary": ("vet_fees",), "pre existing": ("pre_existing",), "pre-existing": ("pre_existing",), "preexisting": ("pre_existing",),
    "deliveroo": ("delivery_use",), "delivery": ("delivery_use",), "courier": ("delivery_use",), "takeaway": ("delivery_use",), "uber": ("hire_or_reward",), "taxi": ("hire_or_reward",), "hire or reward": ("hire_or_reward",),
    "work from home": ("work_from_home", "business_equipment"), "home office": ("work_from_home", "business_equipment"), "freelance": ("business_use", "business_equipment"), "business": ("business_use",), "client": ("business_use",), "commute": ("commuting",), "commuting": ("commuting",),
    "ring": ("jewellery", "valuables"), "necklace": ("jewellery", "valuables"), "watch": ("jewellery", "valuables"), "jewellery": ("jewellery", "valuables"), "jewelry": ("jewellery", "valuables"), "engagement ring": ("jewellery", "valuables"),
    "accidental damage": ("accidental_damage",), "accidentally": ("accidental_damage",), "dropped": ("accidental_damage",), "spilled": ("accidental_damage", "liquid_damage"), "knocked": ("accidental_damage",), "broke": ("accidental_damage",), "broken": ("accidental_damage",),
    "chewed": ("pet_damage",), "scratched": ("pet_damage",), "pet damage": ("pet_damage",),
    "unattended": ("unattended",), "unoccupied": ("unoccupied",), "empty": ("unoccupied",),
    "battery": ("battery",), "charging": ("battery", "charging"), "charger": ("battery", "charging"), "caught fire": ("fire",),
    "liability": ("liability", "third_party"), "third party": ("third_party", "liability"), "someone else": ("third_party",), "neighbour": ("third_party",), "sue": ("liability",), "sued": ("liability",),
    "police": ("police_report",), "crime reference": ("police_report",), "receipt": ("receipt_required",), "receipts": ("receipt_required",), "invoice": ("receipt_required",), "proof of purchase": ("receipt_required",), "proof of ownership": ("proof_of_ownership",),
    "deadline": ("time_limit",), "how long": ("time_limit",), "within": ("time_limit",), "days": ("time_limit",), "renew": ("renewal",), "renewal": ("renewal",), "expire": ("policy_period",), "expires": ("policy_period",),
    "complain": ("complaints",), "complaint": ("complaints",), "ombudsman": ("complaints",), "appeal": ("complaints", "reconsideration"), "reconsider": ("reconsideration",), "rejected": ("reconsideration",), "declined": ("reconsideration",),
    "rent": ("renters",), "renting": ("renters",), "tenant": ("renters",), "landlord": ("landlord", "renters"), "flatmate": ("shared_accommodation",), "housemate": ("shared_accommodation",),
    "garden": ("garden", "outdoors"), "shed": ("outbuilding",), "garage": ("outbuilding",),
    "money": ("money",), "cash": ("money",), "card": ("cards",), "keys": ("keys",), "key": ("keys",),
    "alternative accommodation": ("alternative_accommodation",), "hotel": ("alternative_accommodation",), "uninhabitable": ("alternative_accommodation",),
    "winter sports": ("winter_sports",), "ski": ("winter_sports",), "skiing": ("winter_sports",), "snowboard": ("winter_sports",),
    "drink": ("drink_driving",), "drunk": ("drink_driving", "drunk"), "alcohol": ("drink_driving", "alcohol"),
    "registered": ("registered",), "licence": ("unlicensed",), "license": ("unlicensed",),
    "contents": ("contents",), "belongings": ("personal_possessions",), "possessions": ("personal_possessions",), "valuables": ("valuables",), "away from home": ("away_from_home",), "outside the home": ("away_from_home",), "at work": ("away_from_home",), "cafe": ("away_from_home",), "café": ("away_from_home",), "on the train": ("away_from_home",), "in the car": ("vehicle",),
    "car": ("vehicle", "motor"), "vehicle": ("vehicle",), "van": ("vehicle",), "motor": ("motor",),
    "home": ("home",), "house": ("home", "buildings"), "flat": ("home",), "roof": ("buildings", "storm"), "buildings": ("buildings",), "subsidence": ("subsidence",),
    "travel": ("travel",), "trip": ("travel",), "holiday": ("travel",), "flight": ("travel",), "trips": ("travel",),
    "wear and tear": ("wear_and_tear",), "worn": ("wear_and_tear",), "old": ("wear_and_tear",),
    "fraud": ("fraud",), "exaggerate": ("fraud",),
    # --- 中文
    "被盗": ("theft",), "偷": ("theft",), "盗窃": ("theft",), "盗抢": ("theft", "robbery"), "被抢": ("robbery", "theft"), "抢劫": ("robbery",), "入室": ("theft", "forcible_entry"), "撬": ("forcible_entry",),
    "丢": ("loss",), "遗失": ("loss",), "丢失": ("loss",), "弄丢": ("loss",), "不见了": ("loss",),
    "碎屏": ("screen_damage",), "屏幕": ("screen_damage",), "屏碎": ("screen_damage",), "碎了": ("screen_damage", "accidental_damage"),
    "进水": ("liquid_damage",), "泡水": ("liquid_damage",), "掉水里": ("liquid_damage",),
    "摔": ("accidental_damage",), "跌落": ("accidental_damage",), "摔坏": ("accidental_damage",), "摔了": ("accidental_damage",), "磕": ("accidental_damage",), "意外": ("accidental_damage", "accident"),
    "充电": ("battery", "charging"), "电池": ("battery",), "自燃": ("fire", "battery"), "起火": ("fire",), "着火": ("fire",), "火灾": ("fire",), "烧": ("fire",),
    "外卖": ("delivery_use",), "配送": ("delivery_use",), "送餐": ("delivery_use",), "送外卖": ("delivery_use",), "快递": ("delivery_use",), "跑单": ("delivery_use",), "骑手": ("delivery_use", "rider"), "美团": ("delivery_use",), "饿了么": ("delivery_use",),
    "第三者": ("third_party", "liability"), "撞人": ("third_party", "liability", "collision"), "撞到": ("collision", "third_party"), "撞伤": ("third_party", "injury", "liability"), "别人": ("third_party",), "他人": ("third_party",), "行人": ("third_party",), "赔别人": ("liability", "third_party"), "赔偿责任": ("liability",),
    "受伤": ("injury", "medical"), "医疗": ("medical",), "医药费": ("medical",), "住院": ("medical",), "医院": ("medical",), "伤残": ("personal_accident",), "身故": ("personal_accident", "death"), "骨折": ("injury", "medical"),
    "免赔": ("excess",), "免赔额": ("excess",), "自付": ("excess",), "限额": ("limit",), "最高": ("limit",), "赔多少": ("limit",), "能赔": ("limit",),
    "上牌": ("registered",), "登记": ("registered",), "牌照": ("registered",), "没上牌": ("registered", "unregistered"),
    "改装": ("illegal_modification",), "提速": ("illegal_modification",), "解除限速": ("illegal_modification",),
    "喝酒": ("drunk",), "醉": ("drunk",), "酒驾": ("drunk",),
    "报案": ("police_report",), "报警": ("police_report",), "警察": ("police_report",), "公安": ("police_report",), "交警": ("police_report",), "立案": ("police_report",), "回执": ("police_report",),
    "发票": ("receipt_required", "invoice"), "收据": ("receipt_required",), "凭证": ("receipt_required",), "购机": ("receipt_required", "proof_of_ownership"),
    "维修": ("repair",), "修理": ("repair",), "换屏": ("screen_damage", "repair"), "官方": ("unauthorised_repair",), "售后": ("unauthorised_repair", "repair"),
    "碰撞": ("collision",), "事故": ("accident",), "撞": ("collision",), "翻车": ("collision",), "摔车": ("collision", "own_damage"),
    "暴雨": ("storm", "flood"), "洪水": ("flood",), "台风": ("storm",), "水淹": ("flood",),
    "不保": ("exclusion",), "除外": ("exclusion",), "免责": ("exclusion",), "不赔": ("exclusion",), "保不保": ("coverage_question",), "赔不赔": ("coverage_question",), "能不能赔": ("coverage_question",), "保吗": ("coverage_question",),
    "租房": ("renters",), "出租屋": ("renters",), "房东": ("landlord", "renters"), "合租": ("shared_accommodation",), "家财": ("contents", "home"), "家里": ("home",), "房子": ("home", "buildings"),
    "笔记本": ("laptop",), "电脑": ("laptop", "computer"), "手机": ("phone",), "平板": ("tablet",), "相机": ("valuables",),
    "旅行": ("travel",), "旅游": ("travel",), "出国": ("travel", "geographic_scope"), "出差": ("travel", "business_use"), "航班": ("travel", "delay"), "取消": ("cancellation",), "延误": ("delay",), "行李": ("baggage",), "美国": ("usa",), "加拿大": ("canada",), "香港": ("hong_kong", "geographic_scope"), "境外": ("geographic_scope",),
    "宠物": ("pet",), "狗": ("pet", "dog"), "猫": ("pet",), "宠物医院": ("vet_fees",), "既往症": ("pre_existing",),
    "自行车": ("bicycle",), "单车": ("bicycle",), "电动车": ("ebike",), "电瓶车": ("ebike",), "电单车": ("ebike",), "电动自行车": ("ebike",),
    "汽车": ("vehicle", "motor"), "车险": ("motor",), "车辆": ("vehicle",),
    "到期": ("policy_period", "renewal"), "续保": ("renewal",), "保险期间": ("policy_period",), "生效": ("policy_period",),
    "投诉": ("complaints",), "复核": ("reconsideration",), "复议": ("reconsideration",), "申诉": ("reconsideration", "complaints"), "拒赔": ("reconsideration",), "不服": ("reconsideration",),
    "营运": ("commercial_use", "hire_or_reward"), "载客": ("passenger_carrying", "hire_or_reward"), "拉客": ("passenger_carrying",), "超载": ("overloading",),
    "时限": ("time_limit",), "多久": ("time_limit",), "几天": ("time_limit",), "小时内": ("time_limit",), "期限": ("time_limit",),
    "材料": ("claims_procedure",), "怎么理赔": ("claims_procedure",), "怎么赔": ("claims_procedure",), "理赔流程": ("claims_procedure",), "索赔": ("claims_procedure",), "理赔": ("claims_procedure",),
    "缺口": ("gap_analysis",), "漏保": ("gap_analysis",), "重复": ("duplicate",), "够不够": ("gap_analysis",),
    "房屋": ("buildings", "home"), "财产": ("contents",), "钱": ("money",), "现金": ("money",), "钥匙": ("keys",),
    "责任": ("liability",), "无证": ("unlicensed",), "驾照": ("unlicensed",),
}

SYNONYMS_EN: dict[str, str] = {
    "bikes": "bike", "cycles": "cycle", "phones": "phone", "mobiles": "mobile", "laptops": "laptop",
    "stolen": "theft", "steal": "theft", "stole": "theft", "burglary": "theft", "burgled": "theft", "robbed": "theft", "robbery": "theft",
    "leaking": "leak", "leaked": "leak", "leaks": "leak", "burst": "escape", "flooded": "flood", "flooding": "flood",
    "crashed": "collision", "crash": "collision", "hit": "collision",
    "cancelled": "cancellation", "canceled": "cancellation", "cancel": "cancellation", "cancelling": "cancellation",
    "delayed": "delay", "luggage": "baggage", "suitcase": "baggage",
    "doctor": "medical", "hospital": "medical", "ill": "illness", "sick": "illness", "injured": "injury",
    "deductible": "excess", "america": "usa", "states": "usa",
    "puppy": "dog", "veterinary": "vet", "vets": "vet",
    "deliveroo": "delivery", "courier": "delivery", "deliveries": "delivery", "takeaway": "delivery",
    "jewelry": "jewellery", "ring": "jewellery", "necklace": "jewellery",
    "dropped": "accidental", "accidentally": "accidental", "spilled": "accidental", "broke": "damage", "broken": "damage",
    "chewed": "pets", "windshield": "windscreen",
    "charger": "charging", "charged": "charging",
    "renting": "rent", "tenant": "rent", "rented": "rent",
    "renew": "renewal", "renewed": "renewal", "expire": "expires", "expiry": "expires",
    "appeal": "complaint", "complain": "complaint", "complaints": "complaint",
    "worldwide": "world", "abroad": "world", "overseas": "world",
    "cover": "cover", "covered": "cover", "covers": "cover", "coverage": "cover",
    "exclusion": "exclude", "exclusions": "exclude", "excluded": "exclude", "excludes": "exclude",
}

_SUFFIXES = ("ingly", "edly", "ing", "ied", "ies", "ed", "es", "s")

# very common Chinese characters that carry no retrieval signal on their own
STOPCHARS_ZH = set("的了是我在有和与或及不被将其等时之为以对中上下内外本该并按向由从到所也都就要会能可请你他她它们这那个吗呢吧啊么什怎样多少如果因为所以但是而且然后已经还再又把让给跟着过没很太更最")


TYPE_WORDS: dict[str, tuple[str, ...]] = {
    "ebike": ("e-bike", "ebike", "electric bike", "electric bicycle", "scooter", "电动车", "电瓶车", "电单车", "电动自行车"),
    "gadget": ("phone policy", "phone insurance", "gadget", "handset", "手机保险", "碎屏险", "手机保单", "手机"),
    "travel": ("travel policy", "travel insurance", "holiday", "trip", "abroad", "旅行险", "旅行保险", "旅游险", "出国"),
    "motor": ("car insurance", "motor policy", "car policy", "my car", "vehicle", "windscreen", "车险", "汽车保险", "机动车"),
    "home": ("home insurance", "home policy", "house insurance", "buildings", "contents policy", "家财险", "房屋保险", "家庭财产"),
    "renters": ("renters", "tenants", "tenant", "租客", "租房保险"),
    "pet": ("pet insurance", "pet policy", "dog", "cat", "puppy", "vet", "宠物险", "宠物保险", "宠物"),
}


def detect_insurance_types(text: str) -> set[str]:
    low = text.lower()
    return {t for t, words in TYPE_WORDS.items() if any(w in low for w in words)}


def detect_language(text: str) -> str:
    if not text:
        return "en"
    cjk = len(_CJK.findall(text))
    cjk_chars = sum(len(m) for m in _CJK.findall(text))
    return "zh" if cjk and cjk_chars >= max(2, len(text) * 0.15) else "en"


def _stem(word: str) -> str:
    if len(word) <= 3:
        return word
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            base = word[: -len(suf)]
            if suf in ("ies", "ied"):
                base += "y"
            return base
    return word


def _normalise_en(word: str) -> str:
    w = SYNONYMS_EN.get(word, word)
    w = _stem(w)
    return SYNONYMS_EN.get(w, w)


def extract_tags(text: str) -> set[str]:
    """Map words and phrases in either language to canonical tags."""
    tags: set[str] = set()
    low = text.lower()
    for phrase, phrase_tags in TAG_LEXICON.items():
        if _CJK.search(phrase):
            if phrase in low:
                tags.update(phrase_tags)
        else:
            if " " in phrase or "-" in phrase:
                if phrase in low:
                    tags.update(phrase_tags)
            else:
                if re.search(rf"\b{re.escape(phrase)}s?\b", low):
                    tags.update(phrase_tags)
    return tags


def tokenize(text: str, *, for_query: bool = False) -> list[str]:
    """Tokens for BM25: stemmed English words plus CJK unigrams and bigrams.

    Documents keep unigrams so that single character questions still match;
    queries longer than one character use bigrams only, which avoids short
    clauses winning on incidental character overlap."""
    if not text:
        return []
    low = text.lower()
    tokens: list[str] = []
    for m in _CJK.finditer(low):
        run = m.group(0)
        if not for_query or len(run) == 1:
            tokens.extend(ch for ch in run if ch not in STOPCHARS_ZH)  # unigrams
        for i in range(len(run) - 1):
            bg = run[i : i + 2]
            if bg[0] in STOPCHARS_ZH or bg[1] in STOPCHARS_ZH:
                continue
            tokens.append(bg)  # bigrams
    for w in _WORD.findall(low):
        if w in STOPWORDS_EN or len(w) < 2 and not w.isdigit():
            continue
        tokens.append(_normalise_en(w))
    return tokens


@dataclass
class IndexedClause:
    clause_id: str
    policy_id: str
    policy_number: str
    policy_name: str
    insurance_type: str
    clause_ref: str
    title: str
    section: str
    text: str
    kind: str
    language: str
    tags: list[str]
    amount: float | None = None
    translations: dict = field(default_factory=dict)
    tokens: list[str] = field(default_factory=list)

    @property
    def citation(self) -> str:
        return f"{self.policy_number} §{self.clause_ref}"


@dataclass
class Hit:
    clause: IndexedClause
    score: float
    matched_terms: list[str]
    matched_tags: list[str]

    def to_dict(self, include_text: bool = True) -> dict:
        d = {
            "clause_id": self.clause.clause_id,
            "policy_id": self.clause.policy_id,
            "policy_number": self.clause.policy_number,
            "policy_name": self.clause.policy_name,
            "insurance_type": self.clause.insurance_type,
            "citation": self.clause.citation,
            "clause_ref": self.clause.clause_ref,
            "title": self.clause.title,
            "section": self.clause.section,
            "kind": self.clause.kind,
            "language": self.clause.language,
            "tags": self.clause.tags,
            "amount": self.clause.amount,
            "score": round(self.score, 3),
            "matched_terms": self.matched_terms[:12],
            "matched_tags": self.matched_tags,
        }
        if include_text:
            d["text"] = self.clause.text
            d["translations"] = self.clause.translations
        return d


class ClauseIndex:
    def __init__(self, clauses: Iterable[IndexedClause], k1: float = 1.5, b: float = 0.75) -> None:
        self.clauses = list(clauses)
        self.k1, self.b = k1, b
        self._df: Counter[str] = Counter()
        self._doc_len: list[int] = []
        for c in self.clauses:
            c.tokens = tokenize(f"{c.title} {c.section} {c.text}") + [f"tag:{t}" for t in c.tags] * 2 + [f"kind:{c.kind}"]
            self._doc_len.append(len(c.tokens))
            for t in set(c.tokens):
                self._df[t] += 1
        self._avgdl = (sum(self._doc_len) / len(self._doc_len)) if self._doc_len else 1.0
        self._tf: list[Counter[str]] = [Counter(c.tokens) for c in self.clauses]

    def _idf(self, term: str) -> float:
        n = len(self.clauses)
        df = self._df.get(term, 0)
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(
        self,
        query: str,
        *,
        policy_ids: set[str] | None = None,
        kinds: set[str] | None = None,
        extra_tags: Iterable[str] = (),
        top_k: int = 8,
        min_score: float = 0.0,
        boost_types: Iterable[str] = (),
    ) -> list[Hit]:
        q_tokens = tokenize(query, for_query=True)
        q_tags = extract_tags(query) | set(extra_tags)
        boost = set(boost_types) or detect_insurance_types(query)
        if not q_tokens and not q_tags:
            return []
        q_counts = Counter(q_tokens)
        for t in q_tags:
            q_counts[f"tag:{t}"] += 2  # tags matter more than incidental words
        hits: list[Hit] = []
        for idx, c in enumerate(self.clauses):
            if policy_ids is not None and c.policy_id not in policy_ids:
                continue
            if kinds is not None and c.kind not in kinds:
                continue
            tf = self._tf[idx]
            dl = self._doc_len[idx]
            score = 0.0
            matched: list[str] = []
            for term, qf in q_counts.items():
                f = tf.get(term)
                if not f:
                    continue
                idf = self._idf(term)
                denom = f + self.k1 * (1 - self.b + self.b * dl / self._avgdl)
                score += idf * (f * (self.k1 + 1)) / denom * (1 + 0.3 * (qf - 1))
                matched.append(term)
            if boost and c.insurance_type in boost:
                score *= 1.5  # the question names this kind of policy
            if score > min_score and matched:
                content_terms = [m for m in matched if not m.startswith(("tag:", "kind:"))]
                tag_terms = [m[4:] for m in matched if m.startswith("tag:")]
                hits.append(Hit(clause=c, score=score, matched_terms=content_terms, matched_tags=tag_terms))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]


def index_from_rows(rows) -> ClauseIndex:
    """Build an index from PolicyClause ORM rows (policy relationship loaded)."""
    items = [
        IndexedClause(
            clause_id=r.id,
            policy_id=r.policy_id,
            policy_number=r.policy.policy_number,
            policy_name=f"{r.policy.provider_name} {r.policy.product_name}",
            insurance_type=r.policy.insurance_type,
            clause_ref=r.clause_ref,
            title=r.title,
            section=r.section,
            text=r.text,
            kind=r.kind,
            language=r.language,
            tags=list(r.tags or []),
            amount=r.amount,
            translations=dict(r.translations or {}),
        )
        for r in rows
    ]
    return ClauseIndex(items)


def is_silent(query: str, hits: list[Hit]) -> bool:
    """True when the wording does not appear to address the question at all.

    The question's own content terms and tags must overlap meaningfully with
    the best hit; incidental matches on generic words do not count.
    """
    if not hits:
        return True
    best = hits[0]
    q_tags = extract_tags(query)
    q_terms = {t for t in tokenize(query, for_query=True) if len(t) > 1}
    generic = {"cover", "policy", "insur", "claim", "pay", "保险", "保单", "理赔", "赔", "保", "cover", "exclude"}
    strong_terms = [t for t in best.matched_terms if t not in generic]
    tag_overlap = set(best.matched_tags) & q_tags
    if tag_overlap:
        return False
    if not q_terms:
        return False
    return len(strong_terms) == 0
