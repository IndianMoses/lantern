"""Coverage intelligence: facts, calculator, prioritisation, verification,
the multi-agent pipeline, the graph and the API around them."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.agent import analysts as an
from app.agent.providers.base import ProviderError
from app.config import settings
from app.models import CoverageAnalysisJob, CoverageFinding, CustomerProfile, GraphEdge, GraphNode, Policy, PolicyFact, User
from app.services import analysis, calculator, facts as fact_svc, prioritisation, verification
from app.services import policies as pol_svc


def _user(db, email):
    return db.execute(select(User).where(User.email == email)).scalar_one()


def _policy(db, number) -> Policy:
    return db.execute(select(Policy).where(Policy.policy_number == number)).scalar_one()


# ----------------------------------------------------------------- facts ---


def test_facts_extract_deadlines_waiting_periods_and_not_found(db):
    home = _policy(db, "NGM-HC-204481")
    facts = fact_svc.extract_policy_facts(home, list(home.clauses))
    deadlines = [f for f in facts if f.fact_type == "notification_deadline"]
    assert any(f.value == 30 and f.unit == "days" for f in deadlines)
    assert any(f.value == 24 and f.unit == "hours" for f in deadlines)
    assert all(f.clause_id for f in deadlines) and all(f.text_excerpt for f in deadlines)
    pet = _policy(db, "PAW-DG-331002")
    pf = fact_svc.extract_policy_facts(pet, list(pet.clauses))
    assert any(f.fact_type == "waiting_period" and f.value == 14 and f.unit == "days" for f in pf)
    # a gadget wording has no waiting period: the extractor records nothing invented
    phone = _policy(db, "XHSM-PH-2026-88210")
    ph = fact_svc.extract_policy_facts(phone, list(phone.clauses))
    assert not any(f.fact_type == "waiting_period" and f.value for f in ph)
    # expected items that are absent are explicit not_found facts, never zero
    nf = [f for f in fact_svc.extract_policy_facts(phone, []) if f.status == "not_found"]
    assert {(f.fact_type, f.subject) for f in nf} >= {("coverage", "accidental_damage"), ("notification_deadline", "claims")}
    assert all(f.value is None and "unknown, not as zero" in (f.ambiguity or "") for f in nf)


def test_facts_conflict_needs_two_clauses(db):
    home = _policy(db, "NGM-HC-204481")
    facts = fact_svc.extract_policy_facts(home, list(home.clauses))
    # several figures inside one clause (30 days to notify, 24 hours to report theft) are not a conflict
    assert not any(f.status == "conflicting" and f.fact_type == "notification_deadline" for f in facts)


def test_facts_persist_with_effective_period(db):
    home = _policy(db, "NGM-HC-204481")
    rows = fact_svc.refresh_policy_facts(db, home)
    db.commit()
    assert rows and all(r.effective_from == home.start_date and r.effective_to == home.end_date for r in rows)
    stored = db.execute(select(PolicyFact).where(PolicyFact.policy_id == home.id)).scalars().all()
    assert len(stored) == len(rows)


# ------------------------------------------------------------ calculator ---


def _rule(**kw):
    base = {"rule_id": "r1", "policy_id": "p", "policy_number": "NGM-HC-204481", "covered_event": "theft", "subject": "bicycle", "currency": "GBP", "limit": 350, "excess": 250, "evidence_clause_ids": ["c1"], "calculation_method": "limit_then_excess", "interpretation_status": "validated", "effective_from": date(2026, 1, 1), "effective_to": date(2026, 12, 31)}
    base.update(kw)
    rule, problems = calculator.validate_rule(base)
    assert rule is not None, problems
    return rule, problems


def test_calculator_is_deterministic_with_trace():
    rule, problems = _rule()
    assert problems == []
    a = calculator.calculate(rule, 1200, incident_date=date(2026, 6, 1))
    b = calculator.calculate(rule, 1200, incident_date=date(2026, 6, 1))
    assert a.to_dict() == b.to_dict()
    assert a.payable == 100 and a.uncovered == 1100 and a.status == "illustrative"
    assert [s["step"] for s in a.steps] == ["eligible_loss", "applicable_cap", "apply_cap", "deduct_excess", "uncovered_portion"]
    assert "not an approved payout" in a.label["en"]


def test_calculator_refuses_to_guess():
    rule, _ = _rule(interpretation_status="needs_review")
    with pytest.raises(calculator.RuleNotValidated):
        calculator.calculate(rule, 500)
    rule, problems = _rule(calculation_method="unsupported")
    assert "calculation method not supported" in problems
    with pytest.raises(calculator.UnsupportedCalculation):
        calculator.calculate(rule, 500)
    rule, _ = _rule()
    with pytest.raises(calculator.UnsupportedCalculation):
        calculator.calculate(rule, 500, currency="CNY")
    res = calculator.calculate(rule, 500, incident_date=date(2027, 3, 1))
    assert res.status == "unresolved" and any("outside the policy period" in c for c in res.unresolved_conditions)
    with pytest.raises(calculator.UnsupportedCalculation):
        calculator.calculate(rule, -1)
    bad, problems = calculator.validate_rule({"rule_id": "x", "policy_id": "p", "policy_number": "n", "covered_event": "e", "subject": "s", "currency": "GBP", "limit": -5})
    assert bad is None and problems


# --------------------------------------------------------- prioritisation ---


def test_prioritisation_separates_priority_from_confidence():
    f = {"need": "bicycle", "status": "partial", "severity": "high", "policies": [], "calculation": {"uncovered": 1100}}
    high_conf = prioritisation.prioritise(f, verification={"overall": "verified", "evidence_support": "supported"}, concerns=["Is my bike covered at work?"])
    assert high_conf["risk_priority"] == "critical_to_investigate" and high_conf["evidence_confidence"] == "high"
    low_conf = prioritisation.prioritise(f, verification={"overall": "requires_review", "evidence_support": "weak"}, concerns=["Is my bike covered at work?"])
    assert low_conf["risk_priority"] == "information_needed" and low_conf["evidence_confidence"] == "low"
    covered = prioritisation.prioritise({"need": "contents", "status": "covered", "severity": "low", "policies": []}, verification={"overall": "verified", "evidence_support": "supported"})
    assert covered["risk_priority"] == "low"
    assert all("factor" in r and "weight" in r for r in high_conf["rationale"])


# ------------------------------------------------------------ verification ---


def _ctx(db, email="alex.tan@demo.example"):
    u = _user(db, email)
    pols = pol_svc.policies_with_clauses(db, u.id)
    facts = {p.id: fact_svc.refresh_policy_facts(db, p, c) for p, c in pols}
    prof = db.get(CustomerProfile, u.id)
    return u, pols, verification.build_context(u.id, pols, facts, [a.get("value") or 0 for a in prof.assets])


def test_verification_rejects_unknown_clause_and_qualifies_uncited_exclusions(db):
    u, pols, ctx = _ctx(db)
    home = next(p for p, _ in pols if p.policy_number == "NGM-HC-204481")
    contents = next(c for c in home.clauses if c.kind == "coverage" and "contents" in c.tags)
    bad = {"need": "contents", "status": "covered", "severity": "low", "explanation": {"en": "Covered."}, "evidence": [{"clause_id": "does-not-exist", "policy_number": "NGM-HC-204481", "citation": "NGM-HC-204481 §9.9"}]}
    v = verification.verify(bad, ctx)
    assert v["source_exists"] is False and v["overall"] == "rejected"
    good = {"need": "contents", "status": "covered", "severity": "low", "explanation": {"en": "Your contents are covered inside the home."}, "evidence": [{"clause_id": contents.id, "policy_number": "NGM-HC-204481", "citation": f"NGM-HC-204481 §{contents.clause_ref}"}]}
    v = verification.verify(good, ctx)
    assert v["source_exists"] and v["evidence_support"] == "supported"
    assert v["contradictions"], "exclusions touching contents must be surfaced"
    assert v["overall"] == "qualified"
    # a figure that traces to nothing is flagged
    figure = dict(good, explanation={"en": "Contents are covered up to £123,456 inside the home."})
    v = verification.verify(figure, ctx)
    assert v["rule_consistency"] == "mismatch" and 123456.0 in v["unmatched_figures"]
    # policy numbers and clause references are not treated as claims
    ident = dict(good, explanation={"en": "See NGM-HC-204481 §2.6 and the 2026-11-21 renewal."})
    assert verification.verify(ident, ctx)["rule_consistency"] == "passed"


# --------------------------------------------------------------- pipeline ---


def test_pipeline_deterministic_alex(db):
    u = _user(db, "alex.tan@demo.example")
    ga = analysis.run(db, u, mode="deterministic", job=None)
    db.commit()
    assert ga.mode == "deterministic" and ga.pipeline_version == analysis.PIPELINE_VERSION
    needs = {f["need"]: f for f in ga.findings}
    bike = needs["bicycle"]
    assert bike["status"] == "partial" and bike["calculation"]["status"] == "illustrative" and bike["calculation"]["uncovered"] == 1100
    assert bike["risk_priority"] in {"critical_to_investigate", "high"} and bike["verification"]["overall"] in {"verified", "qualified"}
    assert "rule" in bike["calculation"] and bike["calculation"]["rule"]["interpretation_status"] == "validated"
    # every finding carries evidence trail, verification and prioritisation; every cited clause exists
    clause_ids = {c.id for p, cls in pol_svc.policies_with_clauses(db, u.id) for c in cls}
    for f in ga.findings:
        assert f["verification"]["source_exists"] is True
        assert all(e["clause_id"] in clause_ids for e in f["evidence"])
        assert f["risk_priority"] in {"critical_to_investigate", "high", "medium", "low", "information_needed"}
        assert f["evidence_confidence"] in {"high", "medium", "low"}
        assert f["models"]["judge"]["verdict"] in {"accepted", "revise", "unresolved"}
        assert "id" in f
    # the challenger contributed something the analyst did not (independent method)
    assert any("B" in f["models"]["proposed_by"] for f in ga.findings)
    # the travel medical fact carries its exclusion alongside after the challenge
    tm = needs.get("travel_medical")
    assert tm and any(e.get("note") == "qualification" for e in tm["evidence"])
    # persisted rows and graph
    rows = db.execute(select(CoverageFinding).where(CoverageFinding.analysis_id == ga.id)).scalars().all()
    assert len(rows) == len(ga.findings)
    nodes = db.execute(select(GraphNode).where(GraphNode.analysis_id == ga.id)).scalars().all()
    edges = db.execute(select(GraphEdge).where(GraphEdge.analysis_id == ga.id)).scalars().all()
    keys = {n.key for n in nodes}
    assert any(n.node_type == "policy" and n.data.get("effective_from") for n in nodes)
    assert all(e.source_key in keys and e.target_key in keys for e in edges)
    assert all("kind" in e.provenance and "source" in e.provenance for e in edges)
    assert any(e.relation == "SUPPORTED_BY_EVIDENCE" and e.verified for e in edges)
    assert any(e.relation == "COVERS_ASSET" and not e.verified and e.provenance["kind"] == "inferred" for e in edges)


def test_pipeline_pending_policy_excluded_and_reported(db):
    u = _user(db, "priya.nair@demo.example")
    ga = analysis.run(db, u, mode="deterministic", job=None)
    db.commit()
    assert "MER-ST-70311" not in ga.summary["policies_considered"]
    assert any(x["policy_number"] == "MER-ST-70311" for x in ga.summary["policies_excluded"])
    assert any(f["need"] == "pending_policy" for f in ga.findings)
    travel = next(f for f in ga.findings if f["need"] == "travel")
    assert travel["status"] == "gap" and travel["verification"]["overall"] == "verified"


def test_judge_rejects_invented_clause(db):
    u, pols, ctx = _ctx(db)
    prof = db.get(CustomerProfile, u.id)
    facts = {p.id: fact_svc.refresh_policy_facts(db, p, c) for p, c in pols}
    rules = {r: an.RuleBasedAnalyst(r, profile=prof, policies=pols, facts=facts) for r in ("A", "B", "C")}
    pkg = an.build_package(prof, pols, facts, language="en")
    a_out = rules["A"].analyze(pkg)
    fake = an.CandidateFinding(key="fake", need="contents", title={"en": "x"}, statement={"en": "Covered up to £99,999."}, status="covered", clause_ids=["no-such-clause"], policy_numbers=["NGM-HC-204481"], proposed_by=["A"])
    a_out.findings.append(fake)
    b_out = rules["B"].analyze(pkg)
    ch = rules["B"].challenge(pkg, a_out, b_out)
    assert any(c.target_key == "fake" and c.type == "unsupported" for c in ch.challenges)
    pre = {f.key: verification.verify(analysis._candidate_to_dict(f, {c.id: (p, c) for p, cls in pols for c in cls}), ctx) for f in a_out.findings}
    judge = rules["C"].adjudicate(pkg, a_out, b_out, ch, pre)
    verdict = next(v for v in judge.verdicts if v.key == "fake")
    assert verdict.verdict == "rejected"


def test_challenger_is_independent_of_analyst(db):
    u, pols, _ = _ctx(db)
    prof = db.get(CustomerProfile, u.id)
    facts = {p.id: fact_svc.refresh_policy_facts(db, p, c) for p, c in pols}
    pkg = an.build_package(prof, pols, facts, language="en")
    b1 = an.RuleBasedAnalyst("B", profile=prof, policies=pols, facts=facts).analyze(pkg)
    # running A first changes nothing about B's own assessment
    an.RuleBasedAnalyst("A", profile=prof, policies=pols, facts=facts).analyze(pkg)
    b2 = an.RuleBasedAnalyst("B", profile=prof, policies=pols, facts=facts).analyze(pkg)
    assert [f.key for f in b1.findings] == [f.key for f in b2.findings]
    # the evidence package is minimal: no email, no document bodies, clause text capped
    raw = json.loads(pkg.to_json())
    assert "email" not in json.dumps(raw["customer"])
    assert all(len(c["text"]) <= 600 for p in raw["policies"] for c in p["clauses"])


def test_model_failure_falls_back_and_is_labelled(db, monkeypatch):
    monkeypatch.setattr(settings, "model_a_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "model_b_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "model_c_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "model_timeout_seconds", 0.5)
    monkeypatch.setattr(settings, "model_max_retries", 0)
    u = _user(db, "li.wen@demo.example")
    ga = analysis.run(db, u, mode="models", job=None)
    db.commit()
    assert ga.mode == "deterministic"  # every role fell back, so it is not presented as a model result
    assert all(m["fallback"] is True and m["provider"] == "rules" and m.get("error") for m in ga.models.values())
    assert ga.findings


def test_openai_compatible_parsing_and_retry(monkeypatch):
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, content):
            self._c = content

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": self._c}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResp("not json at all")
            return FakeResp('```json\n{"findings":[{"key":"k","need":"contents","title":{"en":"t","zh":"t"},"statement":{"en":"s","zh":"s"},"status":"gap","severity":"high","kind":"potential_gap","clause_ids":[],"policy_numbers":[],"assumptions":[],"missing_information":["x"],"suggested_action":{"en":"a","zh":"a"},"triggered_by":"","rationale":"r"}],"scenarios":[],"missing_information":[],"notes":[]}\n```')

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    prov = an.OpenAICompatibleAnalyst("A", base_url="http://model.local/v1", model="test-model", max_retries=1)
    pkg = an.EvidencePackage(language="en", customer={}, policies=[], today="2026-10-19")
    out = prov.analyze(pkg)
    assert calls["n"] == 2 and out.findings[0].need == "contents" and out.findings[0].proposed_by == ["A"]
    calls["n"] = 0

    class AlwaysBad(FakeClient):
        def post(self, url, headers=None, json=None):
            return FakeResp("{}{}")

    monkeypatch.setattr(httpx, "Client", AlwaysBad)
    with pytest.raises(ProviderError):
        an.OpenAICompatibleAnalyst("B", base_url="http://model.local/v1", model="m", max_retries=1).analyze(pkg)


# -------------------------------------------------------------------- API ---


def test_gaps_api_job_graph_and_review(alex, priya):
    r = alex.post("/api/gaps/analyze", json={"sync": True})
    assert r.status_code == 200, r.text
    body = r.json()
    job = body["job"]
    assert job["status"] == "completed" and [s["status"] for s in job["stages"]] == ["done"] * 9
    assert job["stages"][3]["detail"]["A"]["provider"] == "rules"
    ga = body["analysis"]
    assert ga["graph"]["nodes"] and ga["graph"]["edges"] and ga["mode"] == "deterministic"
    # job and analysis are scoped to the owner
    assert priya.get(f"/api/gaps/jobs/{job['id']}").status_code == 404
    assert priya.get(f"/api/gaps/{ga['id']}").status_code == 404
    assert alex.get(f"/api/gaps/jobs/{job['id']}").json()["job"]["analysis_id"] == ga["id"]
    # keyboard accessible relationships for a node
    node = next(n for n in ga["graph"]["nodes"] if n["type"] == "finding")
    rel = alex.get(f"/api/gaps/{ga['id']}/graph/{node['key']}").json()
    assert rel["node"]["key"] == node["key"] and rel["relationships"]
    # human review request
    finding = next(f for f in ga["findings"] if f["status"] in {"gap", "partial"})
    r = alex.post(f"/api/gaps/findings/{finding['id']}/review", json={"reason": "please check"})
    assert r.status_code == 200 and r.json()["review_status"] == "requested"
    assert priya.post(f"/api/gaps/findings/{finding['id']}/review", json={"reason": "x"}).status_code == 404
    latest = alex.get("/api/gaps/latest").json()["analysis"]
    assert next(f for f in latest["findings"] if f["id"] == finding["id"])["review_status"] == "requested"
    facts = alex.get("/api/gaps/facts").json()
    assert facts["facts"] and all(f["status"] in {"known", "not_found", "ambiguous", "conflicting", "unknown", "not_applicable"} for f in facts["facts"])
    assert all(f["effective_from"] and f["extractor"].startswith("rules@") for f in facts["facts"])
    assert alex.get("/api/gaps/history").json()["analyses"]


def test_gaps_api_background_job_polls_to_completion(alex, monkeypatch):
    monkeypatch.setattr(settings, "analysis_run_in_background", True)
    r = alex.post("/api/gaps/analyze", json={})
    assert r.status_code == 200
    job = r.json()["job"]
    import time

    for _ in range(100):
        j = alex.get(f"/api/gaps/jobs/{job['id']}").json()["job"]
        if j["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert j["status"] == "completed", j
    assert j["analysis_id"]
    assert alex.get(f"/api/gaps/{j['analysis_id']}").status_code == 200
