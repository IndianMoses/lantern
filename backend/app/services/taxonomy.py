"""Shared vocabulary: insurance types, incident types, the tags each incident
implies, and the dynamic questions asked when an incident is reported.

Labels are bilingual so the interface and the deterministic assistant can
speak either language without a translation service.
"""
from __future__ import annotations

INSURANCE_TYPES: dict[str, dict] = {
    "home": {"en": "Home (buildings and contents)", "zh": "家庭财产（房屋与家财）"},
    "renters": {"en": "Tenants contents", "zh": "租客家财"},
    "motor": {"en": "Motor", "zh": "机动车"},
    "travel": {"en": "Travel", "zh": "旅行"},
    "ebike": {"en": "Electric bicycle", "zh": "电动自行车"},
    "gadget": {"en": "Phone and gadget", "zh": "手机及数码"},
    "pet": {"en": "Pet", "zh": "宠物"},
}

# incident type -> labels, applicable insurance types, implied tags
INCIDENT_TYPES: dict[str, dict] = {
    "theft": {"en": "Theft or burglary", "zh": "盗窃 / 抢劫", "types": ["home", "renters", "motor", "travel", "ebike", "gadget", "pet"], "tags": ["theft", "police_report"]},
    "loss": {"en": "Loss of an item", "zh": "物品遗失", "types": ["home", "renters", "travel", "gadget"], "tags": ["loss"]},
    "escape_of_water": {"en": "Leak or escape of water", "zh": "漏水 / 水管爆裂", "types": ["home", "renters"], "tags": ["escape_of_water", "water_damage"]},
    "flood": {"en": "Flood", "zh": "洪水 / 水淹", "types": ["home", "renters", "motor", "ebike"], "tags": ["flood", "water_damage"]},
    "storm": {"en": "Storm or weather damage", "zh": "风暴 / 恶劣天气", "types": ["home", "renters", "motor", "ebike"], "tags": ["storm"]},
    "fire": {"en": "Fire or smoke", "zh": "火灾 / 烟熏", "types": ["home", "renters", "motor", "ebike"], "tags": ["fire"]},
    "accidental_damage": {"en": "Accidental damage", "zh": "意外损坏", "types": ["home", "renters", "gadget", "ebike"], "tags": ["accidental_damage"]},
    "screen_damage": {"en": "Cracked phone screen", "zh": "手机碎屏", "types": ["gadget"], "tags": ["screen_damage", "accidental_damage", "phone"]},
    "liquid_damage": {"en": "Liquid damage to a device", "zh": "设备进水", "types": ["gadget"], "tags": ["liquid_damage", "accidental_damage", "phone"]},
    "collision": {"en": "Collision or road accident", "zh": "碰撞 / 交通事故", "types": ["motor", "ebike"], "tags": ["collision", "accident", "own_damage"]},
    "third_party_liability": {"en": "Injury or damage to someone else", "zh": "第三者人身或财产损失", "types": ["home", "renters", "motor", "ebike", "pet", "travel"], "tags": ["liability", "third_party"]},
    "windscreen": {"en": "Windscreen or glass", "zh": "挡风玻璃", "types": ["motor"], "tags": ["windscreen", "glass"]},
    "vandalism": {"en": "Vandalism", "zh": "恶意破坏", "types": ["home", "renters", "motor", "ebike"], "tags": ["vandalism", "police_report"]},
    "medical": {"en": "Medical emergency abroad", "zh": "境外医疗", "types": ["travel"], "tags": ["medical", "illness", "injury"]},
    "cancellation": {"en": "Trip cancellation or curtailment", "zh": "行程取消 / 中断", "types": ["travel"], "tags": ["cancellation", "curtailment"]},
    "baggage": {"en": "Lost, stolen or damaged baggage", "zh": "行李遗失、被盗或损坏", "types": ["travel"], "tags": ["baggage", "theft", "loss"]},
    "delay": {"en": "Travel delay or missed departure", "zh": "行程延误 / 误机", "types": ["travel"], "tags": ["delay", "missed_departure"]},
    "illness": {"en": "Pet illness", "zh": "宠物疾病", "types": ["pet"], "tags": ["illness", "vet_fees", "medical"]},
    "injury": {"en": "Pet injury", "zh": "宠物受伤", "types": ["pet"], "tags": ["injury", "vet_fees", "medical"]},
    "rider_injury": {"en": "Rider injured", "zh": "驾驶人受伤", "types": ["ebike"], "tags": ["personal_accident", "injury", "medical"]},
    "other": {"en": "Something else", "zh": "其他", "types": list(INSURANCE_TYPES), "tags": []},
}

LABELS = {
    "en": {
        "required": "Required", "recommended": "Recommended", "optional": "Optional", "not_applicable": "Not applicable", "awaiting_insurer": "Awaiting insurer confirmation",
        "appears_covered": "Appears covered, subject to terms", "possible_exclusion": "A possible exclusion may apply", "unclear": "Unclear from the wording", "not_addressed": "Not addressed by the wording",
    },
    "zh": {
        "required": "必需", "recommended": "建议提供", "optional": "可选", "not_applicable": "不适用", "awaiting_insurer": "待保险公司确认",
        "appears_covered": "看起来在保障范围内（以条款为准）", "possible_exclusion": "可能适用除外责任", "unclear": "条款表述不明确", "not_addressed": "条款未涉及",
    },
}


def incident_tags(incident_type: str) -> list[str]:
    return list(INCIDENT_TYPES.get(incident_type, INCIDENT_TYPES["other"])["tags"])


def incident_label(incident_type: str, lang: str = "en") -> str:
    it = INCIDENT_TYPES.get(incident_type) or INCIDENT_TYPES["other"]
    return it.get(lang, it["en"])


def insurance_label(insurance_type: str, lang: str = "en") -> str:
    t = INSURANCE_TYPES.get(insurance_type) or {"en": insurance_type, "zh": insurance_type}
    return t.get(lang, t["en"])


def incident_types_for(insurance_type: str) -> list[dict]:
    return [{"code": code, **{k: v for k, v in it.items() if k in ("en", "zh")}} for code, it in INCIDENT_TYPES.items() if insurance_type in it["types"]]


# Dynamic incident questions: shown in the claim wizard and asked by the assistant.
BASE_QUESTIONS = [
    {"key": "incident_date", "type": "date", "en": "When did it happen?", "zh": "事情发生在哪一天？", "required": True},
    {"key": "incident_time", "type": "time", "en": "Roughly what time?", "zh": "大概几点？", "required": False},
    {"key": "location", "type": "text", "en": "Where did it happen?", "zh": "发生在哪里？", "required": True},
    {"key": "description", "type": "textarea", "en": "Describe what happened in your own words.", "zh": "请用你自己的话描述经过。", "required": True},
    {"key": "items_involved", "type": "items", "en": "What was damaged, lost or affected? Add an approximate value if you know it.", "zh": "哪些物品受损、遗失或受影响？如知道大致价值请填写。", "required": False},
    {"key": "estimated_loss", "type": "money", "en": "Estimated total loss (leave blank if unknown)", "zh": "预计损失总额（不确定可留空）", "required": False},
    {"key": "actions_taken", "type": "textarea", "en": "What have you done so far? (for example, made it safe, called a plumber, told the police)", "zh": "目前已采取了哪些措施？（例如止损、报修、报警）", "required": False},
    {"key": "safety_concern", "type": "boolean", "en": "Is anyone hurt or in danger right now?", "zh": "现在是否有人受伤或处于危险中？", "required": True},
]

CONDITIONAL_QUESTIONS: dict[str, list[dict]] = {
    "theft": [
        {"key": "authorities_involved", "type": "boolean", "en": "Have you reported it to the police?", "zh": "是否已向公安机关报案？", "required": True},
        {"key": "authority_reference", "type": "text", "en": "Crime or case reference number", "zh": "报案回执 / 受案编号", "required": False},
        {"key": "forcible_entry", "type": "boolean", "en": "Was there forced entry or a broken lock?", "zh": "是否有撬锁或强行进入的痕迹？", "required": False},
    ],
    "vandalism": [
        {"key": "authorities_involved", "type": "boolean", "en": "Have you reported it to the police?", "zh": "是否已向公安机关报案？", "required": True},
        {"key": "authority_reference", "type": "text", "en": "Crime reference number", "zh": "报案编号", "required": False},
    ],
    "collision": [
        {"key": "third_party_involved", "type": "boolean", "en": "Was another vehicle or person involved?", "zh": "是否涉及其他车辆或人员？", "required": True},
        {"key": "authorities_involved", "type": "boolean", "en": "Did the police attend or issue a report?", "zh": "交警是否到场或出具事故认定书？", "required": False},
        {"key": "authority_reference", "type": "text", "en": "Police or accident reference", "zh": "事故认定书编号", "required": False},
        {"key": "third_party_details", "type": "textarea", "en": "Other party's name, contact and insurer, if known", "zh": "对方姓名、联系方式及保险公司（如知道）", "required": False},
    ],
    "third_party_liability": [
        {"key": "third_party_involved", "type": "boolean", "en": "Has the other person made a claim against you or asked for payment?", "zh": "对方是否已向你索赔或要求赔付？", "required": True},
        {"key": "third_party_details", "type": "textarea", "en": "What happened to the other person or their property?", "zh": "对方的人身或财产受到了什么损害？", "required": True},
    ],
    "escape_of_water": [
        {"key": "source_identified", "type": "boolean", "en": "Do you know where the water came from?", "zh": "是否已找到漏水来源？", "required": False},
        {"key": "repairs_done", "type": "boolean", "en": "Has the leak been stopped or repaired?", "zh": "漏水是否已止住或修复？", "required": False},
    ],
    "medical": [
        {"key": "treatment_location", "type": "text", "en": "Which country and hospital or clinic?", "zh": "在哪个国家、哪家医院或诊所就医？", "required": True},
        {"key": "assistance_contacted", "type": "boolean", "en": "Did you contact the insurer's assistance line?", "zh": "是否已联系保险公司的援助热线？", "required": False},
    ],
    "baggage": [
        {"key": "reported_to_carrier", "type": "boolean", "en": "Did you report it to the airline, carrier or police within 24 hours?", "zh": "是否在 24 小时内向航空公司、承运人或警方报告？", "required": True},
        {"key": "authority_reference", "type": "text", "en": "Property Irregularity Report or police reference", "zh": "行李事故报告或报案编号", "required": False},
    ],
    "cancellation": [
        {"key": "cancellation_reason", "type": "text", "en": "Why did you have to cancel or cut the trip short?", "zh": "取消或中断行程的原因？", "required": True},
    ],
    "screen_damage": [
        {"key": "repair_quote_obtained", "type": "boolean", "en": "Do you have a repair quote from an official or authorised repair centre?", "zh": "是否已取得官方或授权维修点的报价？", "required": False},
    ],
    "liquid_damage": [
        {"key": "repair_quote_obtained", "type": "boolean", "en": "Do you have a repair quote from an official or authorised repair centre?", "zh": "是否已取得官方或授权维修点的报价？", "required": False},
    ],
    "illness": [
        {"key": "first_symptoms_date", "type": "date", "en": "When did you first notice symptoms?", "zh": "最早何时发现症状？", "required": True},
        {"key": "vet_practice", "type": "text", "en": "Which veterinary practice treated your pet?", "zh": "在哪家宠物医院就诊？", "required": True},
    ],
    "injury": [
        {"key": "vet_practice", "type": "text", "en": "Which veterinary practice treated your pet?", "zh": "在哪家宠物医院就诊？", "required": True},
    ],
    "rider_injury": [
        {"key": "hospital", "type": "text", "en": "Which hospital treated you?", "zh": "在哪家医院就诊？", "required": True},
        {"key": "authorities_involved", "type": "boolean", "en": "Did the traffic police issue an accident report?", "zh": "交警是否出具事故认定书？", "required": False},
    ],
}


def questions_for(insurance_type: str, incident_type: str) -> list[dict]:
    qs = list(BASE_QUESTIONS)
    qs += CONDITIONAL_QUESTIONS.get(incident_type, [])
    return qs
