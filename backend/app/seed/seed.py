"""Populate a fully fictional demonstration environment.

Run with ``python -m app.seed.seed`` from the backend directory, or
``make seed``. Safe to re run: it wipes and recreates demo data only.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import SessionLocal, init_db
from ..models import (
    AuditEvent,
    Claim,
    ClaimDecision,
    ClaimEvent,
    ClaimStatus,
    Communication,
    Conversation,
    CustomerProfile,
    Document,
    GapAnalysis,
    InformationRequest,
    Message,
    Notification,
    Organization,
    Policy,
    PolicyClause,
    PolicyStatus,
    Roles,
    Submission,
    ToolExecution,
    AgentRun,
    ApprovalRequest,
    User,
    UserSession,
    utcnow,
)
from ..security import hash_password
from .files import make_pdf, make_png, policy_wording_paragraphs
from .policies_en import TEMPLATES_EN
from .policies_zh import TEMPLATES_ZH

TEMPLATES = {**TEMPLATES_EN, **TEMPLATES_ZH}
TODAY = date.today()


def _d(days: int) -> date:
    return TODAY + timedelta(days=days)


def _dt(days: int, hour: int = 10) -> datetime:
    return datetime.combine(_d(days), datetime.min.time()).replace(hour=hour)


# ------------------------------------------------------------- definitions ---

ORGS = [
    {"code": "northgate", "name": "Northgate Mutual", "name_zh": "北门互助保险", "claims_email": "claims@northgate-mutual.example", "country": "GB"},
    {"code": "albion", "name": "Albion Motor Assurance", "name_zh": "阿尔比恩车险", "claims_email": "claims@albion-motor.example", "country": "GB"},
    {"code": "meridian", "name": "Meridian Travel Cover", "name_zh": "子午线旅行险", "claims_email": "claims@meridian-travel.example", "country": "GB"},
    {"code": "castlebay", "name": "Castlebay Underwriting (Harbourline Bank)", "name_zh": "城堡湾承保", "claims_email": "claims@castlebay.example", "country": "GB"},
    {"code": "pawfield", "name": "Pawfield Pet Insurance", "name_zh": "爪田宠物保险", "claims_email": "claims@pawfield.example", "country": "GB"},
    {"code": "pengcheng", "name": "鹏城安保", "name_zh": "鹏城安保", "claims_email": "claims@pengcheng-anbao.example", "country": "CN"},
    {"code": "xinghe", "name": "星河数码保险服务", "name_zh": "星河数码保险服务", "claims_email": "service@xinghe-digital.example", "country": "CN"},
]

STAFF = [
    {"email": "jordan.reyes@northgate.example", "name": "Jordan Reyes", "org": "northgate", "role": Roles.INSURER_HANDLER, "password": "jordan-demo-2026", "locale": "en"},
    {"email": "sam.okafor@northgate.example", "name": "Sam Okafor", "org": "northgate", "role": Roles.INSURER_HANDLER, "password": "sam-demo-2026", "locale": "en"},
    {"email": "morgan.clarke@northgate.example", "name": "Morgan Clarke", "org": "northgate", "role": Roles.INSURER_ADMIN, "password": "morgan-demo-2026", "locale": "en"},
    {"email": "wang.lei@pengcheng.example", "name": "王磊", "org": "pengcheng", "role": Roles.INSURER_HANDLER, "password": "wanglei-demo-2026", "locale": "zh"},
    {"email": "zhou.min@pengcheng.example", "name": "周敏", "org": "pengcheng", "role": Roles.INSURER_ADMIN, "password": "zhoumin-demo-2026", "locale": "zh"},
    {"email": "nia.williams@pawfield.example", "name": "Nia Williams", "org": "pawfield", "role": Roles.INSURER_HANDLER, "password": "nia-demo-2026", "locale": "en"},
    {"email": "lee.park@albion.example", "name": "Lee Park", "org": "albion", "role": Roles.INSURER_HANDLER, "password": "lee-demo-2026", "locale": "en"},
]

CUSTOMERS = [
    {
        "key": "alex", "email": "alex.tan@demo.example", "name": "Alex Tan", "password": "alex-demo-2026", "locale": "en",
        "profile": {
            "country": "GB", "city": "Leamington Spa", "currency": "GBP", "housing": "owner",
            "household": {"adults": 2, "children": 1},
            "assets": [
                {"type": "home", "label": "Two bedroom flat (owned)", "value": 325000, "notes": "contents estimated at 40,000"},
                {"type": "contents", "label": "Household contents", "value": 40000},
                {"type": "car", "label": "2019 hatchback", "value": 11000, "notes": "used for commuting"},
                {"type": "bicycle", "label": "Road bike", "value": 1200, "notes": "ridden to work and locked outside the office"},
                {"type": "laptop", "label": "Laptop used for freelance consulting", "value": 1400},
                {"type": "jewellery", "label": "Engagement ring", "value": 2800},
                {"type": "phone", "label": "Smartphone", "value": 900},
            ],
            "activities": [
                {"type": "travel", "label": "Three trips a year, including a trip to the USA in December", "destinations": ["USA", "Spain"], "trips_per_year": 3},
                {"type": "work_from_home", "label": "Freelance consulting from home two days a week"},
                {"type": "cycling_commute", "label": "Cycles to work"},
                {"type": "commuting_by_car", "label": "Drives to the office three days a week"},
            ],
            "concerns": ["Am I paying twice for travel cover?", "Is my bike covered at work?"],
            "preferences": {"language": "en", "reminders_days": [60, 30, 14, 7]},
            "consents": {"analytics": False, "share_with_insurers_on_submission_only": True},
        },
        "policies": [
            {"template": "homesure", "number": "NGM-HC-204481", "start": _d(-231), "end": _d(134), "premium": 412.0, "freq": "annual", "excess": 250.0},
            {"template": "drivecover", "number": "ALB-MC-77120", "start": _d(-125), "end": _d(240), "premium": 684.0, "freq": "annual", "excess": 500.0},
            {"template": "meridian_travel", "number": "MER-TR-55093", "start": _d(-332), "end": _d(33), "premium": 96.0, "freq": "annual", "excess": 100.0},
            {"template": "harbourline_packaged", "number": "HLB-PK-TR-0092", "start": _d(-200), "end": _d(165), "premium": 180.0, "freq": "annual", "excess": 75.0},
        ],
    },
    {
        "key": "liwen", "email": "li.wen@demo.example", "name": "李雯", "password": "liwen-demo-2026", "locale": "zh",
        "profile": {
            "country": "CN", "city": "深圳南山", "currency": "CNY", "housing": "renter",
            "household": {"adults": 1, "children": 0},
            "assets": [
                {"type": "ebike", "label": "电动自行车（已上牌）", "value": 3200, "notes": "用于即时配送"},
                {"type": "phone", "label": "星耀 S9 手机", "value": 2699},
                {"type": "laptop", "label": "笔记本电脑", "value": 5000},
                {"type": "contents", "label": "出租屋内家庭财产", "value": 20000},
            ],
            "activities": [
                {"type": "delivery_rider", "label": "全职即时配送骑手"},
                {"type": "charges_battery_at_home", "label": "晚上在出租屋内给电池充电"},
            ],
            "concerns": ["充电起火算不算？", "手机被偷有没有赔？"],
            "preferences": {"language": "zh", "reminders_days": [30, 7]},
            "consents": {"analytics": False, "share_with_insurers_on_submission_only": True},
        },
        "policies": [
            {"template": "pengcheng_ebike", "number": "PCAB-EB-2026-10377", "start": _d(-151), "end": _d(214), "premium": 268.0, "freq": "annual", "excess": 200.0},
            # recently started: inside a possible waiting period, which the gap analysis points out
            {"template": "xinghe_phone", "number": "XHSM-PH-2026-88210", "start": _d(-9), "end": _d(356), "premium": 99.0, "freq": "annual", "excess": 0.0},
        ],
    },
    {
        "key": "priya", "email": "priya.nair@demo.example", "name": "Priya Nair", "password": "priya-demo-2026", "locale": "en",
        "profile": {
            "country": "GB", "city": "Bristol", "currency": "GBP", "housing": "renter",
            "household": {"adults": 1, "children": 0},
            "assets": [
                {"type": "contents", "label": "Contents of rented flat", "value": 18000},
                {"type": "laptop", "label": "Laptop used in cafés and the library", "value": 1100},
                {"type": "bicycle", "label": "Hybrid bike", "value": 450},
                {"type": "pet", "label": "Biscuit, four year old beagle", "value": 0},
            ],
            "activities": [
                {"type": "travel", "label": "One European trip a year", "destinations": ["Italy"], "trips_per_year": 1},
                {"type": "hybrid_work", "label": "Works from cafés two days a week"},
            ],
            "concerns": ["My claim for Biscuit was declined", "Is my laptop covered outside the flat?"],
            "preferences": {"language": "en", "reminders_days": [30, 7]},
            "consents": {"analytics": False, "share_with_insurers_on_submission_only": True},
        },
        "policies": [
            {"template": "rentsure", "number": "NGM-TC-118207", "start": _d(-186), "end": _d(179), "premium": 138.0, "freq": "annual", "excess": 100.0},
            {"template": "pawfield_dog", "number": "PAW-DG-331002", "start": _d(-259), "end": _d(106), "premium": 31.0, "freq": "monthly", "excess": 99.0},
            # uploaded by the customer for the Italy trip; extraction not yet confirmed, so not active
            {"template": "meridian_single_trip", "number": "MER-ST-70311", "start": _d(41), "end": _d(52), "premium": 38.0, "freq": "annual", "excess": 75.0, "status": PolicyStatus.PENDING_REVIEW, "source": "uploaded", "confidence": 0.74},
        ],
    },
    {"key": "omar", "email": "omar.haddad@demo.example", "name": "Omar Haddad", "password": "omar-demo-2026", "locale": "en",
     "profile": {"country": "GB", "city": "Leeds", "currency": "GBP", "housing": "owner", "household": {"adults": 2, "children": 2}, "assets": [{"type": "home", "label": "Semi detached house", "value": 290000}, {"type": "contents", "label": "Contents", "value": 35000}], "activities": [], "concerns": [], "preferences": {"language": "en"}, "consents": {}},
     "policies": [{"template": "homesure", "number": "NGM-HC-209912", "start": _d(-300), "end": _d(65), "premium": 455.0, "freq": "annual", "excess": 250.0}]},
    {"key": "chloe", "email": "chloe.bennett@demo.example", "name": "Chloe Bennett", "password": "chloe-demo-2026", "locale": "en",
     "profile": {"country": "GB", "city": "Manchester", "currency": "GBP", "housing": "owner", "household": {"adults": 1, "children": 0}, "assets": [{"type": "contents", "label": "Contents", "value": 28000}], "activities": [], "concerns": [], "preferences": {"language": "en"}, "consents": {}},
     "policies": [{"template": "homesure", "number": "NGM-HC-211305", "start": _d(-90), "end": _d(275), "premium": 33.5, "freq": "monthly", "excess": 250.0}]},
    {"key": "dev", "email": "dev.patel@demo.example", "name": "Dev Patel", "password": "dev-demo-2026", "locale": "en",
     "profile": {"country": "GB", "city": "Birmingham", "currency": "GBP", "housing": "renter", "household": {"adults": 2, "children": 0}, "assets": [{"type": "contents", "label": "Contents", "value": 15000}, {"type": "laptop", "label": "Laptop", "value": 1300}], "activities": [], "concerns": [], "preferences": {"language": "en"}, "consents": {}},
     "policies": [{"template": "rentsure", "number": "NGM-TC-120077", "start": _d(-140), "end": _d(225), "premium": 126.0, "freq": "annual", "excess": 100.0},
                  # six month gadget cover that starts in a fortnight (not yet in force)
                  {"template": "xinghe_phone", "number": "XHSM-PH-2026-90455", "start": _d(14), "end": _d(196), "premium": 59.0, "freq": "annual", "excess": 0.0}]},
    {"key": "sofia", "email": "sofia.rossi@demo.example", "name": "Sofia Rossi", "password": "sofia-demo-2026", "locale": "en",
     "profile": {"country": "GB", "city": "Reading", "currency": "GBP", "housing": "owner", "household": {"adults": 2, "children": 1}, "assets": [{"type": "home", "label": "Terraced house", "value": 410000}, {"type": "contents", "label": "Contents", "value": 45000}], "activities": [], "concerns": [], "preferences": {"language": "en"}, "consents": {}},
     "policies": [{"template": "homesure", "number": "NGM-HC-213840", "start": _d(-210), "end": _d(155), "premium": 489.0, "freq": "annual", "excess": 250.0},
                  # a two week single trip policy that ended recently (different coverage period, recently expired)
                  {"template": "meridian_single_trip", "number": "MER-ST-69002", "start": _d(-33), "end": _d(-19), "premium": 46.0, "freq": "annual", "excess": 75.0, "status": PolicyStatus.EXPIRED}]},
    {"key": "tomas", "email": "tomas.herrera@demo.example", "name": "Tomás Herrera", "password": "tomas-demo-2026", "locale": "en",
     "profile": {"country": "GB", "city": "Nottingham", "currency": "GBP", "housing": "owner", "household": {"adults": 2, "children": 0}, "assets": [{"type": "contents", "label": "Contents", "value": 30000}], "activities": [], "concerns": [], "preferences": {"language": "en"}, "consents": {}},
     "policies": [{"template": "homesure", "number": "NGM-HC-215511", "start": _d(-400), "end": _d(-35), "premium": 430.0, "freq": "annual", "excess": 250.0, "status": PolicyStatus.EXPIRED},
                  {"template": "homesure", "number": "NGM-HC-215511-R", "start": _d(-34), "end": _d(331), "premium": 468.0, "freq": "annual", "excess": 250.0}]},
    {"key": "zhangwei", "email": "zhang.wei@demo.example", "name": "张伟", "password": "zhangwei-demo-2026", "locale": "zh",
     "profile": {"country": "CN", "city": "深圳宝安", "currency": "CNY", "housing": "renter", "household": {"adults": 1, "children": 0}, "assets": [{"type": "ebike", "label": "电动自行车（已上牌）", "value": 2800}], "activities": [{"type": "delivery_rider", "label": "兼职配送"}], "concerns": [], "preferences": {"language": "zh"}, "consents": {}},
     "policies": [{"template": "pengcheng_ebike", "number": "PCAB-EB-2026-11820", "start": _d(-120), "end": _d(245), "premium": 268.0, "freq": "annual", "excess": 200.0}]},
    {"key": "chenxiao", "email": "chen.xiao@demo.example", "name": "陈晓", "password": "chenxiao-demo-2026", "locale": "zh",
     "profile": {"country": "CN", "city": "深圳福田", "currency": "CNY", "housing": "living_with_family", "household": {"adults": 3, "children": 0}, "assets": [{"type": "phone", "label": "星耀 S9 手机", "value": 2699}, {"type": "ebike", "label": "电动自行车（已上牌）", "value": 2400}], "activities": [], "concerns": [], "preferences": {"language": "zh"}, "consents": {}},
     "policies": [{"template": "xinghe_phone", "number": "XHSM-PH-2026-90112", "start": _d(-60), "end": _d(305), "premium": 99.0, "freq": "annual", "excess": 0.0},
                  {"template": "pengcheng_ebike", "number": "PCAB-EB-2026-12044", "start": _d(-100), "end": _d(265), "premium": 268.0, "freq": "annual", "excess": 200.0}]},
]


# ----------------------------------------------------------------- helpers ---


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _doc(db: Session, *, owner: User, path: Path, doc_type: str, claim: Claim | None = None, policy: Policy | None = None, text: str | None = None, key: str | None = None, mime: str | None = None, submitted: bool = False) -> Document:
    mime = mime or ("application/pdf" if path.suffix == ".pdf" else "image/png")
    d = Document(
        owner_user_id=owner.id,
        uploaded_by_user_id=owner.id,
        claim_id=claim.id if claim else None,
        policy_id=policy.id if policy else None,
        filename=path.name,
        mime_type=mime,
        size_bytes=path.stat().st_size,
        sha256=_sha(path),
        storage_path=str(path),
        doc_type=doc_type,
        checklist_item_key=key,
        extraction_status="done" if text else ("not_applicable" if mime.startswith("image/") else "pending"),
        extracted_text=text,
        extraction_meta={"source": "seed"},
        submitted_at=utcnow() if submitted else None,
        is_demo_data=True,
    )
    db.add(d)
    db.flush()
    return d


def _event(db: Session, claim: Claim, actor_type: str, actor_id: str | None, event_type: str, summary: str, when: datetime, data: dict | None = None, visible: bool = True) -> None:
    db.add(ClaimEvent(claim_id=claim.id, actor_type=actor_type, actor_id=actor_id, event_type=event_type, summary=summary, data=data or {}, created_at=when, visible_to_customer=visible))


def _claim_ref(n: int) -> str:
    return f"CLM-{TODAY.year}-{100000 + n}"


# -------------------------------------------------------------------- main ---


def wipe(db: Session) -> None:
    db.execute(update(Policy).values(document_id=None))  # break the policy <-> document cycle
    for table in [
        ToolExecution, Message, GapAnalysis, Submission, Communication, ApprovalRequest, AgentRun, Conversation, Notification,
        ClaimDecision, InformationRequest, ClaimEvent, Document, Claim, PolicyClause, Policy, CustomerProfile,
        UserSession, User, Organization, AuditEvent,
    ]:
        db.execute(delete(table))
    db.commit()


def seed(db: Session, *, demo_dir: Path | None = None) -> dict:
    demo_dir = demo_dir or (settings.upload_dir / "demo")
    demo_dir.mkdir(parents=True, exist_ok=True)
    wipe(db)

    orgs: dict[str, Organization] = {}
    for o in ORGS:
        org = Organization(**o, integration_mode="mock")
        db.add(org)
        orgs[o["code"]] = org
    db.flush()

    staff: dict[str, User] = {}
    for s in STAFF:
        u = User(email=s["email"], password_hash=hash_password(s["password"]), full_name=s["name"], role=s["role"], org_id=orgs[s["org"]].id, locale=s["locale"], is_demo=True)
        db.add(u)
        staff[s["email"]] = u
    db.flush()

    # policy wording PDFs, one per template
    wording_files: dict[str, tuple[Path, str]] = {}
    for code, tpl in TEMPLATES.items():
        pdf_path = demo_dir / "wordings" / f"{code}.pdf"
        paras = policy_wording_paragraphs(tpl)
        make_pdf(pdf_path, f"{tpl['provider']} — {tpl['product']}", paras, header="Policy wording (fictional demonstration document)")
        wording_files[code] = (pdf_path, "\n".join(paras))

    users: dict[str, User] = {}
    policies: dict[str, Policy] = {}
    for c in CUSTOMERS:
        u = User(email=c["email"], password_hash=hash_password(c["password"]), full_name=c["name"], role=Roles.CUSTOMER, locale=c["locale"], is_demo=True)
        db.add(u)
        db.flush()
        db.add(CustomerProfile(user_id=u.id, **c["profile"]))
        users[c["key"]] = u
        for p in c["policies"]:
            tpl = TEMPLATES[p["template"]]
            pol = Policy(
                user_id=u.id,
                insurer_org_id=orgs[tpl["org_code"]].id,
                provider_name=tpl["provider"],
                product_name=tpl["product"],
                insurance_type=tpl["insurance_type"],
                policy_number=p["number"],
                language=tpl["language"],
                country=tpl["country"],
                currency=tpl["currency"],
                start_date=p["start"],
                end_date=p["end"],
                premium_amount=p["premium"],
                premium_frequency=p["freq"],
                excess_amount=p["excess"],
                status=p.get("status", PolicyStatus.ACTIVE),
                source=p.get("source", "seeded"),
                insured_items=[],
                summary=tpl["summary"],
                extraction_confidence=p.get("confidence", 1.0),
                is_demo_data=True,
            )
            db.add(pol)
            db.flush()
            for i, cl in enumerate(tpl["clauses"]):
                db.add(PolicyClause(policy_id=pol.id, clause_ref=cl["ref"], section=cl["section"], title=cl["title"], text=cl["text"], language=tpl["language"], kind=cl["kind"], tags=cl["tags"], amount=cl.get("amount"), position=i, page=1 + i // 6, confidence=p.get("confidence", 1.0), translations=cl.get("translations", {})))
            wpath, wtext = wording_files[p["template"]]
            doc = _doc(db, owner=u, path=wpath, doc_type="policy_wording", policy=pol, text=wtext)
            pol.document_id = doc.id
            policies[p["number"]] = pol
    db.flush()

    from ..services import claims as claim_svc  # local import: services need models loaded

    n = 0
    claims: dict[str, Claim] = {}

    def make_claim(key: str, user: User, policy: Policy, **kw) -> Claim:
        nonlocal n
        n += 1
        cl = Claim(reference=_claim_ref(n), user_id=user.id, policy_id=policy.id, insurer_org_id=policy.insurer_org_id, insurance_type=policy.insurance_type, currency=policy.currency, language=policy.language, is_demo_data=True, **kw)
        db.add(cl)
        db.flush()
        claims[key] = cl
        return cl

    jordan, sam, wanglei, nia, lee = (staff["jordan.reyes@northgate.example"], staff["sam.okafor@northgate.example"], staff["wang.lei@pengcheng.example"], staff["nia.williams@pawfield.example"], staff["lee.park@albion.example"])

    # --- C1 Alex home escape of water: insurer asked for more information
    alex = users["alex"]
    c1 = make_claim("alex_water", alex, policies["NGM-HC-204481"], incident_type="escape_of_water", incident_date=_d(-20), incident_time="07:30", location="Kitchen, home address",
                    description="A pipe joint under the kitchen sink failed overnight. Water soaked the kitchen floor, the base of the cabinets and the laminate flooring in the hallway. We turned off the stopcock, mopped up and called an emergency plumber the same morning.",
                    items_involved=[{"label": "Laminate flooring (kitchen and hallway)", "value": 2100}, {"label": "Kitchen base cabinets", "value": 1400}, {"label": "Emergency plumber call out", "value": 180}],
                    estimated_loss=3680.0, loss_unknown=False, third_party_involved=False, authorities_involved=False, actions_taken="Stopcock closed, emergency plumber replaced the joint, photos taken before drying.",
                    status=ClaimStatus.ADDITIONAL_INFORMATION_REQUESTED, status_source="insurer", status_updated_at=_dt(-12), priority="normal", assigned_handler_id=jordan.id,
                    submitted_at=_dt(-17), acknowledged_at=_dt(-17, 11), first_response_at=_dt(-16), insurer_reference="NGM-ACK-58211")
    photo = make_png(demo_dir / "alex" / "kitchen-water-damage.png")
    invoice = make_pdf(demo_dir / "alex" / "plumber-invoice.pdf", "Invoice — Rapid Flow Plumbing (fictional)", ["# Invoice RF-2291", f"Date: {_d(-20).isoformat()}", "Emergency call out and replacement of failed compression joint under kitchen sink.", "Labour 1.5 hours: £135.00", "Parts: £45.00", "Total: £180.00", "Note: flooring and cabinets show water saturation; recommend specialist drying before replacement."], header="Supplier invoice")
    _doc(db, owner=alex, path=photo, doc_type="photo", claim=c1, key="photos", submitted=True)
    _doc(db, owner=alex, path=invoice, doc_type="receipt", claim=c1, key="invoices", text="Invoice RF-2291 Emergency call out replacement of failed compression joint. Labour 135.00 Parts 45.00 Total 180.00", submitted=True)
    _event(db, c1, "customer", alex.id, "created", "Claim draft created", _dt(-19, 9))
    _event(db, c1, "agent", None, "checklist_generated", "Claim checklist generated from policy conditions (NGM-HC-204481 §4.1, §5.2)", _dt(-19, 9))
    _event(db, c1, "customer", alex.id, "document_uploaded", "Uploaded kitchen-water-damage.png", _dt(-19, 10))
    _event(db, c1, "customer", alex.id, "document_uploaded", "Uploaded plumber-invoice.pdf", _dt(-19, 10))
    _event(db, c1, "customer", alex.id, "approved", "Customer approved submission of the claim pack", _dt(-17, 9))
    _event(db, c1, "adapter", None, "submitted", "Submitted to Northgate Mutual via mock adapter; reference NGM-ACK-58211", _dt(-17, 9))
    _event(db, c1, "insurer", None, "acknowledged", "Northgate Mutual acknowledged receipt", _dt(-17, 11))
    _event(db, c1, "insurer", jordan.id, "assigned", "Assigned to handler Jordan Reyes", _dt(-16))
    _event(db, c1, "insurer", jordan.id, "information_requested", "Handler requested two repair estimates and photographs of the failed joint", _dt(-12))
    db.add(InformationRequest(claim_id=c1.id, requested_by_user_id=jordan.id, message="Thank you for your claim. Under clause 4.1 of your policy we need two estimates for the flooring and cabinet repairs, as the expected cost is above £1,000. Please also send a photograph of the failed joint or the plumber's note confirming the cause.", requested_items=["Two repair estimates", "Photograph of the failed joint or plumber's confirmation of cause"], due_date=_d(9), status="open", created_at=_dt(-12), updated_at=_dt(-12)))
    db.add(Communication(claim_id=c1.id, policy_id=c1.policy_id, user_id=alex.id, insurer_org_id=c1.insurer_org_id, direction="outbound", channel="in_app", kind="claim_notification", subject=f"Claim notification — policy NGM-HC-204481 — escape of water on {_d(-20).isoformat()}", body="Dear Northgate Mutual claims team,\n\nI am writing to notify you of damage at my home caused by a failed pipe joint under the kitchen sink. Photographs and the plumber's invoice are attached. Please confirm receipt and let me know what further information you need.\n\nKind regards,\nAlex Tan", recipient="claims@northgate-mutual.example", status="sent", generated_by="agent", sent_at=_dt(-17, 9), sender_user_id=alex.id, created_at=_dt(-17, 9), updated_at=_dt(-17, 9)))
    db.add(Communication(claim_id=c1.id, policy_id=c1.policy_id, user_id=alex.id, insurer_org_id=c1.insurer_org_id, direction="inbound", channel="in_app", kind="information_request", subject="Further information needed — claim " + c1.reference, body="Thank you for your claim. We need two estimates for the flooring and cabinet repairs and a photograph of the failed joint. Please upload them to your claim.", recipient=alex.email, status="received", generated_by="insurer_staff", sent_at=_dt(-12), sender_user_id=jordan.id, created_at=_dt(-12), updated_at=_dt(-12)))
    db.add(Submission(claim_id=c1.id, idempotency_key=f"seed:{c1.id}", adapter="mock_insurer", mode="integrated", request_payload={"claim_reference": c1.reference, "documents": 2}, response_payload={"status": "accepted", "insurer_reference": "NGM-ACK-58211", "mock": True}, status="succeeded", created_at=_dt(-17, 9)))
    db.add(Notification(user_id=alex.id, kind="information_requested", title="Northgate Mutual needs more information", body="Two repair estimates and a photograph of the failed joint are requested for claim " + c1.reference, link=f"/claims/{c1.id}", created_at=_dt(-12)))
    db.add(Notification(user_id=alex.id, kind="renewal", title="Meridian travel policy renews in 33 days", body="MER-TR-55093 ends on " + _d(33).isoformat() + ". Compare it with the travel cover included in your Harbourline account before renewing.", link="/policies", created_at=_dt(-1)))

    # --- C2 Alex motor windscreen: settled
    c2 = make_claim("alex_windscreen", alex, policies["ALB-MC-77120"], incident_type="windscreen", incident_date=_d(-98), location="M40 northbound", description="A stone thrown up by a lorry chipped the windscreen; the chip spread into a crack across the driver's side within two days.", items_involved=[{"label": "Windscreen replacement", "value": 287}], estimated_loss=287.0, status=ClaimStatus.SETTLED, status_source="insurer", status_updated_at=_dt(-80), assigned_handler_id=lee.id, submitted_at=_dt(-96), acknowledged_at=_dt(-96, 12), first_response_at=_dt(-95), insurer_reference="ALB-ACK-10944")
    _event(db, c2, "customer", alex.id, "created", "Claim draft created", _dt(-97))
    _event(db, c2, "adapter", None, "submitted", "Submitted to Albion Motor Assurance via mock adapter; reference ALB-ACK-10944", _dt(-96))
    _event(db, c2, "insurer", lee.id, "decision_recorded", "Approved: windscreen replacement at approved repairer, £75 excess applies", _dt(-92))
    _event(db, c2, "insurer", lee.id, "status_changed", "Settled: £212 paid to repairer", _dt(-80))
    db.add(ClaimDecision(claim_id=c2.id, decided_by_user_id=lee.id, outcome="approved", reason="Windscreen replacement covered under clause 2.3. Replacement excess of £75 applies.", clause_refs=["2.3"], approved_amount=212.0, currency="GBP", created_at=_dt(-92)))

    # --- C3 Li Wen e-bike theft: under review
    liwen = users["liwen"]
    c3 = make_claim("liwen_theft", liwen, policies["PCAB-EB-2026-10377"], incident_type="theft", incident_date=_d(-16), incident_time="23:40", location="南山区某小区楼下非机动车停放处",
                    description="收工后把电动车停在小区楼下的非机动车停放处并上了锁，第二天早上发现整车不见了。当天上午去派出所报案并取得了报案回执。", items_involved=[{"label": "电动自行车整车（含电池）", "value": 3200}],
                    estimated_loss=3200.0, third_party_involved=False, authorities_involved=True, authority_reference="深公（南）受案字〔2026〕第 0417 号", actions_taken="已报警并取得回执，已查看小区监控并向物业登记。",
                    status=ClaimStatus.UNDER_REVIEW, status_source="insurer", status_updated_at=_dt(-13), assigned_handler_id=wanglei.id, submitted_at=_dt(-15), acknowledged_at=_dt(-15, 14), first_response_at=_dt(-14), insurer_reference="PCAB-ACK-20931")
    receipt = make_pdf(demo_dir / "liwen" / "报案回执.pdf", "受案回执（虚构演示文件）", ["# 深圳市公安局南山分局", f"报案人：李雯    报案时间：{_d(-15).isoformat()} 09:12", "案由：电动自行车被盗", "受案编号：深公（南）受案字〔2026〕第 0417 号", "备注：本回执仅用于演示。"], header="公安机关受案回执")
    purchase = make_pdf(demo_dir / "liwen" / "购车发票.pdf", "增值税普通发票（虚构演示文件）", ["# 购车发票", "商品名称：电动自行车（含锂电池）", "金额：¥3,200.00", f"开票日期：{_d(-160).isoformat()}", "销售方：鹏城车行（虚构）"], header="购车发票")
    ebike_photo = make_png(demo_dir / "liwen" / "停放处照片.png", colour=(205, 198, 180))
    _doc(db, owner=liwen, path=receipt, doc_type="police_report", claim=c3, key="police_report", text="深圳市公安局南山分局 受案回执 报案人 李雯 案由 电动自行车被盗 受案编号 深公（南）受案字〔2026〕第0417号", submitted=True)
    _doc(db, owner=liwen, path=purchase, doc_type="receipt", claim=c3, key="proof_of_ownership", text="增值税普通发票 电动自行车（含锂电池） 金额 3200.00", submitted=True)
    _doc(db, owner=liwen, path=ebike_photo, doc_type="photo", claim=c3, key="photos", submitted=True)
    _event(db, c3, "customer", liwen.id, "created", "创建理赔草稿", _dt(-15, 8))
    _event(db, c3, "agent", None, "checklist_generated", "根据保单第三条、第九条生成理赔清单", _dt(-15, 8))
    _event(db, c3, "customer", liwen.id, "approved", "客户确认提交理赔材料包", _dt(-15, 13))
    _event(db, c3, "adapter", None, "submitted", "通过模拟接口提交至鹏城安保，受理编号 PCAB-ACK-20931", _dt(-15, 13))
    _event(db, c3, "insurer", None, "acknowledged", "鹏城安保已确认收到", _dt(-15, 14))
    _event(db, c3, "insurer", wanglei.id, "assigned", "分配给理赔员王磊", _dt(-14))
    _event(db, c3, "insurer", wanglei.id, "status_changed", "进入审核；根据第三条，自报案之日起满 60 日未追回方可赔付", _dt(-13))
    db.add(Communication(claim_id=c3.id, policy_id=c3.policy_id, user_id=liwen.id, insurer_org_id=c3.insurer_org_id, direction="outbound", channel="in_app", kind="claim_notification", subject=f"理赔通知 — 保单 PCAB-EB-2026-10377 — {_d(-16).isoformat()} 整车被盗", body="鹏城安保理赔部：\n\n我投保的电动自行车于上述日期在小区楼下停放处被盗，已向公安机关报案并取得受案回执，相关材料已随本申请提交。请确认受理并告知后续所需材料。\n\n李雯", recipient="claims@pengcheng-anbao.example", status="sent", generated_by="agent", sent_at=_dt(-15, 13), sender_user_id=liwen.id, created_at=_dt(-15, 13), updated_at=_dt(-15, 13)))
    db.add(Notification(user_id=liwen.id, kind="status_changed", title="理赔进入审核", body=f"{c3.reference} 已由鹏城安保受理并进入审核。", link=f"/claims/{c3.id}", created_at=_dt(-13)))

    # --- C4 Priya pet claim declined
    priya = users["priya"]
    c4 = make_claim("priya_pet", priya, policies["PAW-DG-331002"], incident_type="illness", incident_date=_d(-90), location="Bristol", description="Biscuit developed red, itchy patches on his belly and paws and was scratching constantly. The vet diagnosed an allergic skin condition and prescribed a steroid course and medicated shampoo, with a follow up visit two weeks later.",
                    items_involved=[{"label": "Consultation and diagnosis", "value": 68}, {"label": "Steroid course and medicated shampoo", "value": 94}, {"label": "Follow up consultation", "value": 52}], estimated_loss=214.0, status=ClaimStatus.DECLINED, status_source="insurer", status_updated_at=_dt(-69), assigned_handler_id=nia.id, submitted_at=_dt(-77), acknowledged_at=_dt(-77, 15), first_response_at=_dt(-75), insurer_reference="PAW-ACK-30177")
    vet_invoice = make_pdf(demo_dir / "priya" / "vet-invoice.pdf", "Invoice — Clifton Vets (fictional)", ["# Invoice CV-7731", "Patient: Biscuit (beagle, 4y)", f"Date: {_d(-90).isoformat()}", "Consultation 68.00; steroid course 61.00; medicated shampoo 33.00", f"Follow up {_d(-76).isoformat()}: 52.00", "Total: £214.00"], header="Veterinary invoice")
    _doc(db, owner=priya, path=vet_invoice, doc_type="receipt", claim=c4, key="invoices", text="Clifton Vets invoice Biscuit beagle consultation 68 steroid course 61 medicated shampoo 33 follow up 52 total 214", submitted=True)
    _event(db, c4, "customer", priya.id, "created", "Claim draft created", _dt(-78))
    _event(db, c4, "adapter", None, "submitted", "Submitted to Pawfield Pet Insurance via mock adapter; reference PAW-ACK-30177", _dt(-77))
    _event(db, c4, "insurer", nia.id, "assigned", "Assigned to handler Nia Williams", _dt(-75))
    _event(db, c4, "insurer", nia.id, "decision_recorded", "Declined: pre existing condition (clauses 1.1 and 2.3)", _dt(-69))
    db.add(ClaimDecision(claim_id=c4.id, decided_by_user_id=nia.id, outcome="declined", reason=f"The clinical history supplied by Clifton Vets records a consultation on {_d(-280).isoformat()} for itching and a skin rash on the abdomen, which is before the policy start date of {_d(-259).isoformat()}. Under clause 1.1 the current allergic skin condition is treated as a pre existing condition and is excluded by clause 2.3.", clause_refs=["1.1", "2.3"], approved_amount=0.0, currency="GBP", created_at=_dt(-69)))
    db.add(Communication(claim_id=c4.id, policy_id=c4.policy_id, user_id=priya.id, insurer_org_id=c4.insurer_org_id, direction="inbound", channel="in_app", kind="decision", subject="Decision on claim " + c4.reference, body=f"We have reviewed Biscuit's claim and the clinical history from Clifton Vets. The history records a consultation on {_d(-280).isoformat()} for itching and a skin rash, before your policy started on {_d(-259).isoformat()}. Under clauses 1.1 and 2.3 the condition is pre existing and the claim is declined. If you have further veterinary evidence you may ask us to review this decision (clause 5.1).", recipient=priya.email, status="received", generated_by="insurer_staff", sent_at=_dt(-69), sender_user_id=nia.id, created_at=_dt(-69), updated_at=_dt(-69)))
    db.add(Notification(user_id=priya.id, kind="decision", title="Decision issued on Biscuit's claim", body="Pawfield declined the claim citing clauses 1.1 and 2.3. You can ask the assistant to explain the decision and prepare a review request.", link=f"/claims/{c4.id}", created_at=_dt(-69)))

    # --- Northgate queue volume
    omar, chloe, dev, sofia, tomas = users["omar"], users["chloe"], users["dev"], users["sofia"], users["tomas"]
    c5 = make_claim("omar_storm", omar, policies["NGM-HC-209912"], incident_type="storm", incident_date=_d(-4), location="Roof, home address", description="High winds overnight lifted and broke a number of roof tiles; rain then came through the loft into the back bedroom ceiling.", items_involved=[{"label": "Roof tiles and ridge", "value": 1800}, {"label": "Bedroom ceiling repair and redecoration", "value": 800}], estimated_loss=2600.0, status=ClaimStatus.SUBMITTED, status_source="adapter", status_updated_at=_dt(-2), submitted_at=_dt(-2), insurer_reference="NGM-ACK-58477", priority="high")
    _event(db, c5, "adapter", None, "submitted", "Submitted to Northgate Mutual via mock adapter; reference NGM-ACK-58477", _dt(-2))
    c6 = make_claim("chloe_theft", chloe, policies["NGM-HC-211305"], incident_type="theft", incident_date=_d(-10), incident_time="14:00", location="Home address", description="Returned from work to find the rear patio door forced open. A laptop, a games console, a watch and some cash were taken.", items_involved=[{"label": "Laptop", "value": 1200}, {"label": "Games console", "value": 450}, {"label": "Watch", "value": 1900}, {"label": "Cash", "value": 150}, {"label": "Patio door repair", "value": 3200}], estimated_loss=6900.0, authorities_involved=True, authority_reference="GMP/2026/0441872", status=ClaimStatus.ACKNOWLEDGED, status_source="insurer", status_updated_at=_dt(-7), assigned_handler_id=sam.id, submitted_at=_dt(-8), acknowledged_at=_dt(-7), first_response_at=_dt(-7), insurer_reference="NGM-ACK-58390")
    _event(db, c6, "adapter", None, "submitted", "Submitted to Northgate Mutual via mock adapter; reference NGM-ACK-58390", _dt(-8))
    _event(db, c6, "insurer", sam.id, "assigned", "Assigned to handler Sam Okafor", _dt(-7))
    c7 = make_claim("dev_water", dev, policies["NGM-TC-120077"], incident_type="escape_of_water", incident_date=_d(-28), location="Living room", description="Water came through the ceiling from the flat above after their washing machine hose split, soaking the sofa and the laptop on the coffee table.", items_involved=[{"label": "Sofa", "value": 650}, {"label": "Laptop", "value": 1250}], estimated_loss=1900.0, status=ClaimStatus.UNDER_REVIEW, status_source="insurer", status_updated_at=_dt(-20), assigned_handler_id=jordan.id, submitted_at=_dt(-25), acknowledged_at=_dt(-25), first_response_at=_dt(-23), insurer_reference="NGM-ACK-58002")
    _event(db, c7, "adapter", None, "submitted", "Submitted to Northgate Mutual via mock adapter; reference NGM-ACK-58002", _dt(-25))
    _event(db, c7, "insurer", jordan.id, "status_changed", "Under review: loss adjuster report requested", _dt(-20))
    c8 = make_claim("sofia_fire", sofia, policies["NGM-HC-213840"], incident_type="fire", incident_date=_d(-43), location="Kitchen", description="A pan of oil caught fire while cooking. The fire was put out with a fire blanket but the extractor hood, wall units and ceiling were badly scorched and smoke damaged.", items_involved=[{"label": "Extractor hood and wall units", "value": 2900}, {"label": "Ceiling repair and redecoration", "value": 1550}], estimated_loss=4450.0, status=ClaimStatus.APPROVED, status_source="insurer", status_updated_at=_dt(-5), assigned_handler_id=jordan.id, submitted_at=_dt(-40), acknowledged_at=_dt(-40), first_response_at=_dt(-38), insurer_reference="NGM-ACK-57655")
    _event(db, c8, "adapter", None, "submitted", "Submitted to Northgate Mutual via mock adapter; reference NGM-ACK-57655", _dt(-40))
    _event(db, c8, "insurer", jordan.id, "decision_recorded", "Approved £4,200 after £250 excess", _dt(-5))
    db.add(ClaimDecision(claim_id=c8.id, decided_by_user_id=jordan.id, outcome="approved", reason="Fire damage to buildings and contents is covered under clauses 2.1 and 2.2. Standard excess of £250 deducted under clause 5.1.", clause_refs=["2.1", "2.2", "5.1"], approved_amount=4200.0, currency="GBP", created_at=_dt(-5)))
    c9 = make_claim("tomas_tv", tomas, policies["NGM-HC-215511"], incident_type="accidental_damage", incident_date=_d(-64), location="Living room", description="The wall bracket failed and the television fell, cracking the screen.", items_involved=[{"label": "Television", "value": 1400}], estimated_loss=1400.0, status=ClaimStatus.SETTLED, status_source="insurer", status_updated_at=_dt(-38), assigned_handler_id=sam.id, submitted_at=_dt(-62), acknowledged_at=_dt(-62), first_response_at=_dt(-60), insurer_reference="NGM-ACK-57102")
    _event(db, c9, "adapter", None, "submitted", "Submitted to Northgate Mutual via mock adapter; reference NGM-ACK-57102", _dt(-62))
    _event(db, c9, "insurer", sam.id, "decision_recorded", "Approved £1,150 under clause 2.4 after £250 excess", _dt(-48))
    _event(db, c9, "insurer", sam.id, "status_changed", "Settled: £1,150 paid", _dt(-38))
    db.add(ClaimDecision(claim_id=c9.id, decided_by_user_id=sam.id, outcome="approved", reason="Accidental damage to home entertainment equipment is covered under clause 2.4 even without the Accidental Damage extension. £250 excess deducted.", clause_refs=["2.4", "5.1"], approved_amount=1150.0, currency="GBP", created_at=_dt(-48)))

    # --- 鹏城 queue volume
    zhangwei, chenxiao = users["zhangwei"], users["chenxiao"]
    c10 = make_claim("zhangwei_collision", zhangwei, policies["PCAB-EB-2026-11820"], incident_type="collision", incident_date=_d(-7), incident_time="18:20", location="宝安区新安街道路口", description="送单途中在路口与一名横穿马路的行人相撞，行人手臂擦伤，电动车前叉和车灯损坏。交警出具了事故认定书，双方同等责任。", items_involved=[{"label": "电动车前叉与车灯维修", "value": 1800}, {"label": "行人医疗费用", "value": 2400}], estimated_loss=4200.0, third_party_involved=True, authorities_involved=True, authority_reference="深交认字〔2026〕宝 0932 号", status=ClaimStatus.ACKNOWLEDGED, status_source="insurer", status_updated_at=_dt(-5), assigned_handler_id=wanglei.id, submitted_at=_dt(-6), acknowledged_at=_dt(-5), first_response_at=_dt(-5), insurer_reference="PCAB-ACK-21104")
    _event(db, c10, "adapter", None, "submitted", "通过模拟接口提交至鹏城安保，受理编号 PCAB-ACK-21104", _dt(-6))
    _event(db, c10, "insurer", wanglei.id, "assigned", "分配给理赔员王磊", _dt(-5))
    c11 = make_claim("chenxiao_screen", chenxiao, policies["XHSM-PH-2026-90112"], incident_type="screen_damage", incident_date=_d(-3), location="家中", description="手机从桌上滑落到瓷砖地面，屏幕右上角碎裂并出现绿线，触控部分失灵。", items_involved=[{"label": "官方售后换屏报价", "value": 1099}], estimated_loss=1099.0, status=ClaimStatus.SUBMITTED, status_source="adapter", status_updated_at=_dt(-2), submitted_at=_dt(-2), insurer_reference="XHSM-ACK-77310")
    _event(db, c11, "adapter", None, "submitted", "通过模拟接口提交至星河数码，受理编号 XHSM-ACK-77310", _dt(-2))

    db.flush()

    # checklist, coverage view and completeness for every seeded claim
    for cl in claims.values():
        claim_svc.refresh_claim_analysis(db, cl)

    # audit genesis entries
    audit.record(db, actor_type="system", actor_id=None, action="seed.demo_environment", details={"customers": len(CUSTOMERS), "staff": len(STAFF), "policies": len(policies), "claims": len(claims)}, commit=False)
    db.commit()
    return {"customers": len(CUSTOMERS), "staff": len(STAFF), "policies": len(policies), "claims": len(claims), "organizations": len(ORGS)}


def demo_accounts() -> list[dict]:
    accounts = [{"email": c["email"], "password": c["password"], "name": c["name"], "role": "customer", "locale": c["locale"]} for c in CUSTOMERS]
    accounts += [{"email": s["email"], "password": s["password"], "name": s["name"], "role": s["role"], "org": s["org"], "locale": s["locale"]} for s in STAFF]
    return accounts


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        result = seed(db)
        print("Seeded demo environment:", result)
        print("Demo accounts (development only):")
        for a in demo_accounts():
            print(f"  {a['role']:16} {a['email']:36} {a['password']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
