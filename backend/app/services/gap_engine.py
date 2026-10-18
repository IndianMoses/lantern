"""Coverage gap analysis.

Needs are derived from the customer's own profile (assets, housing,
activities, household). Each need is then checked against the clauses of
the customer's active policies using the shared tag vocabulary:

* covered   - an active coverage clause addresses the need
* partial   - covered, but a stated limit is below the declared value, or an
              exclusion or condition narrows it
* gap       - no active policy addresses the need
* duplicate - two or more policies cover the same need
* note      - something to think about that is out of scope for a product
              comparison (life or income protection, for example)

Every finding carries the clauses it relies on. Findings are potential
issues for the customer to check with their insurer, not determinations.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import CustomerProfile, Policy, PolicyClause


@dataclass
class Finding:
    need: str
    label_en: str
    label_zh: str
    status: str
    severity: str
    explanation_en: str
    explanation_zh: str
    action_en: str
    action_zh: str
    triggered_by: str
    evidence: list[dict] = field(default_factory=list)
    policies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "need": self.need, "label": {"en": self.label_en, "zh": self.label_zh}, "status": self.status, "severity": self.severity,
            "explanation": {"en": self.explanation_en, "zh": self.explanation_zh}, "suggested_action": {"en": self.action_en, "zh": self.action_zh},
            "triggered_by": self.triggered_by, "evidence": _dedupe(self.evidence), "policies": sorted(set(self.policies)),
        }


def _dedupe(evidence: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for e in evidence:
        if e["clause_id"] in seen:
            continue
        seen.add(e["clause_id"])
        out.append(e)
    return out


def _cite(cl: PolicyClause, note: str = "") -> dict:
    return {"clause_id": cl.id, "policy_number": cl.policy.policy_number, "clause_ref": cl.clause_ref, "citation": f"{cl.policy.policy_number} §{cl.clause_ref}", "title": cl.title, "kind": cl.kind, "amount": cl.amount, "note": note}


class _Portfolio:
    def __init__(self, policies: list[tuple[Policy, list[PolicyClause]]]):
        self.items = [(p, c) for p, c in policies if p.status == "active"]

    def coverage(self, tags: set[str], *, types: set[str] | None = None, all_tags: bool = True, exclude_not_selected: bool = True) -> list[tuple[Policy, PolicyClause]]:
        out = []
        for p, clauses in self.items:
            if types and p.insurance_type not in types:
                continue
            for cl in clauses:
                if cl.kind != "coverage":
                    continue
                ct = set(cl.tags or [])
                if exclude_not_selected and "not_selected" in ct:
                    continue
                ok = tags <= ct if all_tags else bool(tags & ct)
                if ok:
                    out.append((p, cl))
        return out

    def not_selected(self, tags: set[str], *, types: set[str] | None = None) -> list[tuple[Policy, PolicyClause]]:
        out = []
        for p, clauses in self.items:
            if types and p.insurance_type not in types:
                continue
            for cl in clauses:
                ct = set(cl.tags or [])
                if cl.kind == "coverage" and "not_selected" in ct and tags & ct:
                    out.append((p, cl))
        return out

    def exclusions(self, tags: set[str], *, types: set[str] | None = None) -> list[tuple[Policy, PolicyClause]]:
        out = []
        for p, clauses in self.items:
            if types and p.insurance_type not in types:
                continue
            for cl in clauses:
                if cl.kind == "exclusion" and tags & set(cl.tags or []):
                    out.append((p, cl))
        return out

    def conditions(self, tags: set[str], *, types: set[str] | None = None) -> list[tuple[Policy, PolicyClause]]:
        out = []
        for p, clauses in self.items:
            if types and p.insurance_type not in types:
                continue
            for cl in clauses:
                if cl.kind in {"condition", "definition"} and tags & set(cl.tags or []):
                    out.append((p, cl))
        return out

    def of_type(self, t: str) -> list[tuple[Policy, list[PolicyClause]]]:
        return [(p, c) for p, c in self.items if p.insurance_type == t]


def _money(v: float | None, cur: str) -> str:
    return f"{v:,.0f} {cur}" if v is not None else "?"


def analyze(profile: CustomerProfile, policies: list[tuple[Policy, list[PolicyClause]]]) -> dict:
    pf = _Portfolio(policies)
    findings: list[Finding] = []
    assets = profile.assets or []
    activities = profile.activities or []
    act_types = {a.get("type") for a in activities}
    cur = profile.currency or "GBP"

    def asset(t: str):
        return [a for a in assets if a.get("type") == t]

    # ---- home / contents
    if profile.housing == "owner" or asset("home"):
        hits = pf.coverage({"buildings"}, types={"home"})
        if hits:
            findings.append(Finding("buildings", "Buildings cover", "房屋主体保障", "covered", "low", "Your home policy covers the building against the listed insured events.", "你的家庭财产保单对房屋主体承保所列的保险事故。", "Check the buildings sum insured still reflects the rebuild cost.", "确认房屋保额仍能覆盖重建成本。", "You own your home", [_cite(c) for _, c in hits[:1]], [p.policy_number for p, _ in hits]))
        else:
            findings.append(Finding("buildings", "Buildings cover", "房屋主体保障", "gap", "high", "You own your home but no active policy covers the building itself.", "你拥有住房，但没有有效保单承保房屋主体。", "If you have a mortgage the lender usually requires buildings cover; compare home policies.", "如有按揭，贷款机构通常要求投保房屋险；请比较家庭财产保险。", "You own your home"))
    if profile.housing in {"owner", "renter"} or asset("contents"):
        hits = pf.coverage({"contents"}, types={"home", "renters"})
        contents_assets = asset("contents")
        declared = sum(float(a.get("value") or 0) for a in contents_assets)
        if hits:
            limit = max((c.amount or 0) for _, c in hits)
            if declared and limit and declared > limit:
                findings.append(Finding("contents", "Contents cover", "家庭财产（家财）保障", "partial", "medium", f"Contents are covered, but you declared {_money(declared, cur)} of contents against a sum insured of {_money(limit, cur)}.", f"家财已有保障，但你申报的家财价值 {_money(declared, cur)} 高于保额 {_money(limit, cur)}。", "Ask the insurer to raise the contents sum insured to avoid under insurance.", "请保险公司提高家财保额以避免不足额投保。", f"Contents valued at {_money(declared, cur)}", [_cite(c) for _, c in hits[:1]], [p.policy_number for p, _ in hits]))
            else:
                findings.append(Finding("contents", "Contents cover", "家庭财产（家财）保障", "covered", "low", "Your contents are covered inside the home against the listed insured events.", "你的家财在住所内对所列保险事故有保障。", "Keep an inventory of higher value items with receipts.", "保留高价值物品清单及凭证。", "Where you live", [_cite(c) for _, c in hits[:1]], [p.policy_number for p, _ in hits]))
        else:
            findings.append(Finding("contents", "Contents cover", "家庭财产（家财）保障", "gap", "high", f"Nothing in your portfolio covers the contents of your home{(' (declared ' + _money(declared, cur) + ')') if declared else ''}. Fire, theft and water damage to your belongings would be uninsured.", f"你的保单中没有承保住所内家财{('（申报价值 ' + _money(declared, cur) + '）') if declared else ''}的保障。火灾、盗窃或水浸造成的财物损失将无法获赔。", "Consider contents insurance; for renters it is usually inexpensive.", "建议考虑家财险；租客家财险通常费用不高。", "Where you live and your belongings"))
    if profile.housing == "renter":
        hits = pf.coverage({"tenants_liability"}, types={"renters"})
        pet_excl = [x for x in pf.exclusions({"pet_damage"}, types={"renters", "home"})]
        if hits and asset("pet") and pet_excl:
            findings.append(Finding("tenants_liability", "Tenant's liability to landlord", "租客对房东的责任", "partial", "medium", "Your tenant's liability cover excludes damage caused by pets, and you have a pet. Damage your pet does to the landlord's fixtures would not be covered.", "你的租客责任保障不包含宠物造成的损坏，而你养有宠物。宠物对房东固定装置造成的损坏不在保障范围内。", "Ask whether a pet damage extension is available, or check whether your pet policy's liability section responds.", "询问是否可加保宠物损坏扩展，或确认宠物保单的责任险部分是否适用。", "You rent and have a pet", [_cite(c) for _, c in hits[:1]] + [_cite(c, "exclusion") for _, c in pet_excl[:1]], [p.policy_number for p, _ in hits]))
        elif hits:
            findings.append(Finding("tenants_liability", "Tenant's liability to landlord", "租客对房东的责任", "covered", "low", "Accidental damage to the landlord's fixtures is covered up to the stated limit.", "对房东固定装置的意外损坏有保障，以限额为准。", "Check the limit against your deposit and the tenancy terms.", "将限额与押金及租约条款对照。", "You rent", [_cite(c) for _, c in hits[:1]], [p.policy_number for p, _ in hits]))
        else:
            findings.append(Finding("tenants_liability", "Tenant's liability to landlord", "租客对房东的责任", "gap", "medium", "As a tenant you can be liable for accidental damage to the landlord's property; no policy covers that.", "作为租客，你可能需对房东财产的意外损坏负责，目前没有保单承保。", "Tenants contents policies usually include this; compare options.", "租客家财险通常包含此项，请比较方案。", "You rent"))

    # ---- motor
    if asset("car"):
        tp = pf.coverage({"third_party", "liability"}, types={"motor"})
        own = pf.coverage({"own_damage"}, types={"motor"}) or pf.coverage({"vehicle", "accidental_damage"}, types={"motor"})
        if tp:
            findings.append(Finding("motor_liability", "Motor third party liability", "机动车第三者责任", "covered", "low", "Your motor policy covers liability to other people.", "你的车险包含对第三者的责任保障。", "Make sure the class of use matches how you use the car.", "确认使用性质与实际用车方式一致。", "You own a car", [_cite(c) for _, c in tp[:1]], [p.policy_number for p, _ in tp]))
            if "delivery_rider" in act_types or "delivery" in act_types:
                excl = pf.exclusions({"delivery_use", "hire_or_reward"}, types={"motor"})
                if excl:
                    findings.append(Finding("motor_delivery_use", "Delivery use of the car", "用车从事配送", "gap", "high", "You do delivery work, and your motor policy excludes hire or reward including delivery.", "你从事配送工作，而车险除外条款不保包括配送在内的营运用途。", "Tell the insurer about delivery use; a hire and reward extension or a different policy is needed.", "向保险公司申报配送用途，需加保营运扩展或更换保单。", "Delivery work", [_cite(c, "exclusion") for _, c in excl[:1]], [p.policy_number for p, _ in excl]))
        else:
            findings.append(Finding("motor_liability", "Motor third party liability", "机动车第三者责任", "gap", "high", "You own a car but no active motor policy is recorded. Third party cover is a legal requirement for driving.", "你拥有汽车，但没有记录有效车险。第三者责任险是上路的法定要求。", "Add your motor policy or arrange cover before driving.", "请添加车险保单或在上路前投保。", "You own a car"))
        if asset("car") and tp and not own:
            findings.append(Finding("motor_own_damage", "Damage to your own car", "本车损失", "gap", "medium", "Your motor cover does not appear to include damage to your own vehicle.", "你的车险似乎不包含本车损失保障。", "Consider comprehensive cover if the car's value justifies it.", "如车辆价值较高，建议考虑车损险。", "You own a car"))

    # ---- e-bike
    if asset("ebike"):
        tp = pf.coverage({"third_party"}, types={"ebike"})
        theft = pf.coverage({"theft"}, types={"ebike"})
        pa = pf.coverage({"personal_accident"}, types={"ebike"})
        value = float(asset("ebike")[0].get("value") or 0)
        if tp:
            findings.append(Finding("ebike_liability", "E-bike third party liability", "电动自行车第三者责任", "covered", "low", "Injury or damage you cause to others while riding is covered up to the stated limit.", "骑行时对他人造成的人身或财产损失有保障，以限额为准。", "Keep the vehicle registered; unregistered vehicles are not covered.", "保持车辆登记上牌状态；未上牌车辆不在保障范围内。", "You own an e-bike", [_cite(c) for _, c in tp[:1]], [p.policy_number for p, _ in tp]))
        else:
            findings.append(Finding("ebike_liability", "E-bike third party liability", "电动自行车第三者责任", "gap", "high", "You ride an e-bike but have no liability cover for injury or damage to others.", "你骑电动自行车，但没有对他人人身或财产损失的责任保障。", "E-bike third party policies are inexpensive; compare options.", "电动自行车第三者责任险费用不高，请比较方案。", "You own an e-bike"))
        if theft:
            limit = max((c.amount or 0) for _, c in theft)
            if value and limit and value > limit:
                findings.append(Finding("ebike_theft", "E-bike theft", "电动自行车盗抢", "partial", "medium", f"Theft of the whole vehicle is covered, but the limit ({_money(limit, cur)}) is below the value you declared ({_money(value, cur)}). Theft of the battery alone is not covered.", f"整车盗抢有保障，但限额（{_money(limit, cur)}）低于你申报的价值（{_money(value, cur)}），且仅电池被盗不赔。", "Ask whether a higher theft limit is available; always remove or lock the battery.", "询问是否可提高盗抢限额；电池应随身取下或上锁。", f"E-bike valued at {_money(value, cur)}", [_cite(c) for _, c in theft[:1]], [p.policy_number for p, _ in theft]))
            else:
                findings.append(Finding("ebike_theft", "E-bike theft", "电动自行车盗抢", "covered", "low", "Theft of the whole vehicle is covered subject to the police report and waiting period conditions.", "整车盗抢有保障，需满足报案和等待期条件。", "Report any theft to the police within 24 hours.", "被盗后 24 小时内报案。", "You own an e-bike", [_cite(c) for _, c in theft[:1]], [p.policy_number for p, _ in theft]))
        if pa:
            findings.append(Finding("ebike_rider_accident", "Rider personal accident", "驾驶人意外伤害", "partial", "medium", f"Rider injury is covered but the medical limit is modest ({_money(max((c.amount or 0) for _, c in pa), cur)} for death or disability); medical costs are reimbursed within a small limit.", "驾驶人意外伤害有保障，但保额较低，医疗费用报销限额较小。", "If riding is your livelihood, consider a separate personal accident or income protection policy.", "如以骑行为生计，建议另行考虑个人意外险或收入保障。", "You ride an e-bike", [_cite(c) for _, c in pa[:1]], [p.policy_number for p, _ in pa]))
        if "charges_battery_at_home" in act_types:
            excl = pf.exclusions({"battery", "charging"}, types={"ebike"})
            fire_cover = pf.coverage({"fire", "contents"}, types={"home", "renters"})
            if excl and not fire_cover:
                findings.append(Finding("battery_fire", "Fire while charging the battery at home", "在家充电引发火灾", "gap", "high", "Your e-bike policy excludes fire caused during charging, and you have no home or contents policy that would cover fire damage to your belongings or liability to neighbours.", "你的电动车保单不保充电过程中引起的火灾，而你也没有家财保单来承保火灾造成的财物损失或对邻居的责任。", "Charge outside living areas where possible, and consider a renters contents policy with fire and liability cover.", "尽量在居住区域外充电，并考虑投保含火灾和责任保障的租客家财险。", "You charge the battery at home", [_cite(c, "exclusion") for _, c in excl[:1]], [p.policy_number for p, _ in excl]))
            elif excl:
                findings.append(Finding("battery_fire", "Fire while charging the battery at home", "在家充电引发火灾", "partial", "medium", "The e-bike policy excludes charging fires; damage to your home contents would fall to your home policy instead.", "电动车保单不保充电火灾；家中财物损失需由家财保单承担。", "Check your home policy's conditions on charging e-bikes indoors.", "查看家财保单对室内充电的相关条件。", "You charge the battery at home", [_cite(c, "exclusion") for _, c in excl[:1]] + [_cite(c) for _, c in fire_cover[:1]]))
        if "delivery_rider" in act_types:
            cond = pf.conditions({"delivery_use", "declared_use"}, types={"ebike"})
            if cond:
                findings.append(Finding("ebike_delivery_use", "Delivery use declared", "配送用途申报", "covered", "low", "Your schedule records that delivery use was declared, so accidents during deliveries are within the policy's use conditions.", "保单附表已载明申报即时配送用途，配送途中的事故符合使用性质条件。", "Keep the declaration current if your work changes.", "工作性质变化时及时更新申报。", "You work as a delivery rider", [_cite(c) for _, c in cond[:1]], [p.policy_number for p, _ in cond]))

    # ---- bicycle
    for a in asset("bicycle"):
        value = float(a.get("value") or 0)
        home_cov = pf.coverage({"bicycle"}, types={"home", "renters"})
        away_cov = pf.coverage({"bicycle", "away_from_home"}, types={"home", "renters"})
        if not home_cov:
            findings.append(Finding("bicycle", "Bicycle", "自行车", "gap", "medium", f"Your bicycle ({_money(value, cur)}) is not covered by any policy.", f"你的自行车（{_money(value, cur)}）没有任何保单承保。", "Consider specifying it on a contents policy or a standalone cycle policy.", "考虑在家财险中列明或投保单车险。", a.get("label", "Bicycle")))
            continue
        limit = max((c.amount or 0) for _, c in home_cov)
        if value and limit and value > limit:
            findings.append(Finding("bicycle", "Bicycle away from home", "自行车（离家时）", "partial", "high", f"Your bicycle is worth {_money(value, cur)} but the policy pays at most {_money(limit, cur)} for an unspecified cycle, and away from home only when locked to a fixed object. It is effectively uninsured for most of its value.", f"你的自行车价值 {_money(value, cur)}，但保单对未列明的自行车最多赔 {_money(limit, cur)}，且离家时须锁在固定物上。大部分价值实际上没有保障。", "Ask the insurer to list the bike individually in the schedule (specified item) or buy a cycle policy.", "请保险公司在附表中单独列明该自行车，或购买单车险。", a.get("label", "Bicycle"), [_cite(c) for _, c in home_cov[:1]] + [_cite(c) for _, c in away_cov[:1]], [p.policy_number for p, _ in home_cov]))
        else:
            findings.append(Finding("bicycle", "Bicycle", "自行车", "covered", "low", "Your bicycle is within the policy's cycle limit.", "你的自行车在保单单车限额之内。", "Lock it to a fixed object when away from home.", "离家时锁在固定物上。", a.get("label", "Bicycle"), [_cite(c) for _, c in home_cov[:1]], [p.policy_number for p, _ in home_cov]))

    # ---- laptop and work equipment
    for a in asset("laptop"):
        value = float(a.get("value") or 0)
        business = ("work_from_home" in act_types or "freelance" in act_types or "business" in (a.get("label", "").lower()) or "freelance" in (a.get("label", "").lower()))
        away = ("hybrid_work" in act_types or any(k in (a.get("label", "").lower() + a.get("notes", "").lower()) for k in ("café", "cafe", "library", "train", "office")))
        if business:
            be = pf.coverage({"business_equipment"}, types={"home", "renters"})
            if be:
                findings.append(Finding("business_equipment", "Equipment used for work at home", "居家办公设备", "covered", "low", f"Office equipment used for clerical work at home is covered up to {_money(max((c.amount or 0) for _, c in be), cur)}.", "居家文书类工作使用的办公设备有保障，以限额为准。", "Business stock and clients' property are not covered; keep the use clerical.", "营业存货及客户财产不在保障范围内。", a.get("label", "Laptop"), [_cite(c) for _, c in be[:1]], [p.policy_number for p, _ in be]))
            else:
                findings.append(Finding("business_equipment", "Equipment used for work at home", "居家办公设备", "gap", "medium", "You use equipment for work at home, and household policies normally exclude business use unless it is specifically covered.", "你在家使用设备工作，而家财保单通常不保商业用途，除非特别约定。", "Tell your insurer about home working and ask about business equipment cover.", "向保险公司说明居家办公并询问办公设备保障。", a.get("label", "Laptop")))
        if away:
            pp = pf.coverage({"personal_possessions", "away_from_home"}, types={"home", "renters"})
            ns = pf.not_selected({"personal_possessions", "away_from_home"}, types={"home", "renters"})
            if pp:
                findings.append(Finding("laptop_away", "Laptop outside the home", "笔记本（离家时）", "covered", "low", "Personal possessions away from home are covered, subject to the single item limit.", "离家携带的个人物品有保障，以单件限额为准。", "Check the single item limit against the laptop's value.", "将单件限额与笔记本价值对照。", a.get("label", "Laptop"), [_cite(c) for _, c in pp[:1]], [p.policy_number for p, _ in pp]))
            elif ns:
                findings.append(Finding("laptop_away", "Laptop outside the home", "笔记本（离家时）", "gap", "high", f"You use your laptop ({_money(value, cur)}) outside the home, but the personal possessions extension on your contents policy is not selected, so it is only covered while inside the home.", f"你在家外使用笔记本（{_money(value, cur)}），但家财保单的个人物品扩展未选择，因此只在住所内有保障。", "Ask the insurer to add the personal possessions extension, or consider gadget cover.", "请保险公司加保个人物品扩展，或考虑数码设备险。", a.get("label", "Laptop"), [_cite(c, "not selected") for _, c in ns[:1]], [p.policy_number for p, _ in ns]))
            elif not pf.coverage({"laptop"}, types={"gadget", "travel"}):
                findings.append(Finding("laptop_away", "Laptop outside the home", "笔记本（离家时）", "gap", "medium", "Nothing covers your laptop outside the home.", "没有保单承保你在家外使用的笔记本。", "Consider personal possessions cover or a gadget policy.", "考虑个人物品保障或数码设备险。", a.get("label", "Laptop")))
        if not business and not away and not pf.coverage({"contents"}, types={"home", "renters"}) and not pf.coverage({"laptop"}, types={"gadget"}):
            findings.append(Finding("laptop_home", "Laptop", "笔记本电脑", "gap", "medium", f"Your laptop ({_money(value, cur)}) is not covered by any policy, even at home.", f"你的笔记本（{_money(value, cur)}）没有任何保单承保，即使在家中也没有。", "A contents policy would cover it at home for fire, theft and water damage.", "家财险可在家中对火灾、盗窃和水浸提供保障。", a.get("label", "Laptop")))

    # ---- phone
    for a in asset("phone"):
        value = float(a.get("value") or 0)
        gadget = pf.coverage({"phone"}, types={"gadget"})
        gadget_theft_excl = pf.exclusions({"theft"}, types={"gadget"})
        pp_phone = pf.coverage({"personal_possessions", "away_from_home"}, types={"home", "renters"})
        travel_gadget = pf.coverage({"gadget", "phone"}, types={"travel"})
        if gadget:
            if gadget_theft_excl:
                findings.append(Finding("phone", "Phone", "手机", "partial", "medium", "Your phone policy covers screen and accidental damage, but theft and loss are excluded, and no other policy covers the phone away from home.", "你的手机保单承保碎屏和意外损坏，但不保盗抢和遗失，也没有其他保单在家外承保手机。", "If theft is a concern, look for a gadget policy that includes theft, or add the phone to a contents policy's personal possessions section.", "如担心被盗，可选择含盗抢责任的数码设备险，或将手机列入家财险的个人物品保障。", a.get("label", "Phone"), [_cite(c) for _, c in gadget[:1]] + [_cite(c, "exclusion") for _, c in gadget_theft_excl[:1]], [p.policy_number for p, _ in gadget]))
            else:
                findings.append(Finding("phone", "Phone", "手机", "covered", "low", "Your phone is covered by a gadget policy.", "你的手机有数码设备保单承保。", "Keep the IMEI and purchase invoice to hand.", "保存 IMEI 和购机发票。", a.get("label", "Phone"), [_cite(c) for _, c in gadget[:1]], [p.policy_number for p, _ in gadget]))
        elif pp_phone or travel_gadget:
            ev = [_cite(c) for _, c in pp_phone[:1]] + [_cite(c) for _, c in travel_gadget[:1]]
            findings.append(Finding("phone", "Phone", "手机", "partial", "low", "Your phone has some cover through personal possessions or travel gadget sections, each with a sub limit, but no standalone gadget policy for everyday accidental damage.", "你的手机通过个人物品或旅行数码保障有部分保障（各有分项限额），但没有针对日常意外损坏的独立设备险。", "Check the phone sub limit against its value.", "将手机分项限额与其价值对照。", a.get("label", "Phone"), ev, list({p.policy_number for p, _ in pp_phone + travel_gadget})))
        else:
            findings.append(Finding("phone", "Phone", "手机", "gap", "low", f"Your phone ({_money(value, cur)}) is not covered outside the home.", f"你的手机（{_money(value, cur)}）在家外没有保障。", "Consider gadget cover if replacing it would be a strain.", "如更换手机会造成负担，可考虑数码设备险。", a.get("label", "Phone")))

    # ---- jewellery and single item limits
    for a in asset("jewellery"):
        value = float(a.get("value") or 0)
        val_def = pf.conditions({"single_item_limit", "valuables"}, types={"home", "renters"})
        cover = pf.coverage({"contents"}, types={"home", "renters"})
        limit = max([c.amount or 0 for _, c in val_def] or [0])
        if cover and limit and value > limit:
            findings.append(Finding("valuables", "High value item: " + a.get("label", "jewellery"), "贵重物品：" + a.get("label", "首饰"), "partial", "high", f"Your {a.get('label', 'item').lower()} is worth {_money(value, cur)} but the policy's single item limit for valuables is {_money(limit, cur)} unless the item is listed in the schedule.", f"你的{a.get('label', '物品')}价值 {_money(value, cur)}，但保单对贵重物品的单件限额为 {_money(limit, cur)}，除非在附表中列明。", "Ask the insurer to specify the item (a valuation may be needed).", "请保险公司单独列明该物品（可能需要估价证明）。", a.get("label", "Jewellery"), [_cite(c) for _, c in val_def[:1]], [p.policy_number for p, _ in val_def]))
        elif not cover:
            findings.append(Finding("valuables", "High value item: " + a.get("label", "jewellery"), "贵重物品", "gap", "medium", "No contents policy covers this item.", "没有家财保单承保该物品。", "Consider contents cover with the item specified.", "考虑投保家财险并列明该物品。", a.get("label", "Jewellery")))

    # ---- pet
    if asset("pet"):
        vet = pf.coverage({"vet_fees"}, types={"pet"})
        liab = pf.coverage({"liability", "dog"}, types={"pet"})
        if vet:
            findings.append(Finding("pet_vet_fees", "Veterinary fees", "宠物医疗费用", "covered", "low", "Vet fees are covered up to the annual limit, with pre existing conditions excluded.", "宠物医疗费用在年度限额内有保障，既往症除外。", "Keep vaccinations and annual checks up to date; they are policy conditions.", "按时接种疫苗和年检，这是保单条件。", "You have a pet", [_cite(c) for _, c in vet[:1]], [p.policy_number for p, _ in vet]))
        else:
            findings.append(Finding("pet_vet_fees", "Veterinary fees", "宠物医疗费用", "gap", "medium", "Your pet's vet bills are not insured.", "你的宠物医疗费用没有保险。", "Compare pet policies early; conditions that appear first become pre existing.", "尽早比较宠物保险；先出现的病症会被视为既往症。", "You have a pet"))
        if liab:
            findings.append(Finding("pet_liability", "Liability for your dog", "宠物第三者责任", "covered", "low", "Injury or damage your dog causes to others is covered.", "你的狗对他人造成的伤害或损失有保障。", "Note the exclusion for damage to property you own or rent.", "注意对自有或租住财产损坏的除外。", "You have a dog", [_cite(c) for _, c in liab[:1]], [p.policy_number for p, _ in liab]))

    # ---- travel
    travel_acts = [a for a in activities if a.get("type") == "travel"]
    if travel_acts:
        travel_policies = pf.of_type("travel")
        medical = pf.coverage({"medical"}, types={"travel"})
        if not travel_policies:
            findings.append(Finding("travel", "Travel insurance", "旅行保险", "gap", "high", "You travel but have no travel policy. Emergency medical costs abroad are the main risk.", "你有出行，但没有旅行保险。境外紧急医疗费用是主要风险。", "Single trip or annual travel cover; check medical limits and declare conditions.", "可选单次或年度旅行险；注意医疗限额并如实申报健康状况。", travel_acts[0].get("label", "Travel")))
        else:
            if len(travel_policies) > 1:
                ev = []
                for p, clauses in travel_policies:
                    for cl in clauses:
                        if cl.kind == "coverage" and "medical" in (cl.tags or []):
                            ev.append(_cite(cl))
                            break
                findings.append(Finding("travel_duplicate", "Two travel policies", "两份旅行保险", "duplicate", "medium", f"You have {len(travel_policies)} travel policies ({', '.join(p.policy_number for p, _ in travel_policies)}). Both cover emergency medical, cancellation and baggage. Insurers only pay once for the same loss, so you are likely paying twice for overlapping cover.", f"你有 {len(travel_policies)} 份旅行保险（{'、'.join(p.policy_number for p, _ in travel_policies)}），都包含紧急医疗、取消和行李保障。同一损失只能获赔一次，你可能在为重叠的保障重复付费。", "Compare the two before the next renewal and keep the one whose area and limits match your trips.", "在下次续保前比较两者，保留地域范围和限额更符合行程的一份。", "Your travel policies", ev, [p.policy_number for p, _ in travel_policies]))
            destinations = {d.upper() for a in travel_acts for d in (a.get("destinations") or [])}
            if {"USA", "US", "UNITED STATES", "CANADA"} & destinations:
                excl = pf.conditions({"excl_usa_canada"}, types={"travel"})
                incl = pf.conditions({"usa", "worldwide"}, types={"travel"})
                incl = [(p, c) for p, c in incl if "excl_usa_canada" not in (c.tags or [])]
                if excl and not incl:
                    findings.append(Finding("travel_usa", "Trip to the USA", "美国行程", "gap", "high", f"Your travel policy excludes the USA and Canada, and you plan a USA trip.", "你的旅行保险不含美国和加拿大，而你计划前往美国。", "Upgrade the area of cover or buy a single trip policy including the USA before you travel.", "出行前升级地域范围或购买含美国的单次旅行险。", "USA trip", [_cite(c, "area excludes USA") for _, c in excl[:1]], [p.policy_number for p, _ in excl]))
                elif excl and incl:
                    findings.append(Finding("travel_usa", "Trip to the USA", "美国行程", "partial", "medium", f"{excl[0][0].policy_number} excludes the USA, but {incl[0][0].policy_number} includes it. Rely on the second policy for that trip and check its conditions (trip length, age, account requirements).", f"{excl[0][0].policy_number} 不含美国，但 {incl[0][0].policy_number} 包含。该行程应依赖后者，并核对其条件（行程天数、年龄、账户要求）。", "Take the right policy documents with you.", "出行时携带正确的保单文件。", "USA trip", [_cite(c, "area excludes USA") for _, c in excl[:1]] + [_cite(c, "area includes USA") for _, c in incl[:1]], [excl[0][0].policy_number, incl[0][0].policy_number]))
            if medical:
                findings.append(Finding("travel_medical", "Emergency medical abroad", "境外紧急医疗", "covered", "low", "Emergency medical treatment abroad is covered, subject to declaring pre existing conditions.", "境外紧急医疗有保障，需如实申报既往病史。", "Contact the assistance line before hospital admission.", "入院前联系援助热线。", travel_acts[0].get("label", "Travel"), [_cite(c) for _, c in medical[:1]], [p.policy_number for p, _ in medical]))

    # ---- household notes (out of scope for product comparison)
    hh = profile.household or {}
    if (hh.get("children") or 0) > 0 or (hh.get("dependants") or 0) > 0:
        findings.append(Finding("protection_note", "Dependants: income and life protection", "有受抚养人：收入与寿险保障", "note", "low", "You have dependants. Life and income protection are outside this analysis, which only reads general insurance policies, but they are worth a separate review.", "你有受抚养人。寿险和收入保障不在本分析范围内（本分析只读取财产类保单），但值得单独评估。", "Speak to a regulated adviser about protection cover.", "就保障型保险咨询持牌顾问。", "Household"))

    status_order = {"gap": 0, "partial": 1, "duplicate": 2, "note": 3, "covered": 4}
    sev_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (status_order.get(f.status, 9), sev_order.get(f.severity, 9)))
    counts = {k: sum(1 for f in findings if f.status == k) for k in ("gap", "partial", "duplicate", "covered", "note")}
    return {
        "findings": [f.to_dict() for f in findings],
        "summary": {"counts": counts, "policies_considered": [p.policy_number for p, _ in pf.items], "assets_considered": len(assets), "activities_considered": len(activities)},
        "disclaimer": {
            "en": "These are potential issues derived from your profile and the clauses of your policies. They are not coverage determinations or regulated advice; confirm with your insurer before relying on them.",
            "zh": "以上是根据你的资料和保单条款得出的潜在问题，不是保险公司的保障结论，也不构成受监管的建议；依赖前请向保险公司确认。",
        },
    }
