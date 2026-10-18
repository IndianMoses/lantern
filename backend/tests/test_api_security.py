"""Authorisation, tenant isolation, CSRF, approvals and rate limiting."""
from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.models import Claim


def test_unauthenticated_requests_are_rejected(client):
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/insurer/claims").status_code == 401
    assert client.post("/api/claims", json={"incident_type": "theft"}).status_code == 401


def test_bad_password_denied(client):
    r = client.post("/api/auth/login", json={"email": "alex.tan@demo.example", "password": "wrong-password-1"})
    assert r.status_code == 401


def test_csrf_required_for_state_changes(alex):
    r = alex.client.post("/api/assistant/conversations", json={})  # no header
    assert r.status_code == 403
    r = alex.post("/api/assistant/conversations", json={})
    assert r.status_code == 200


def test_role_boundaries(alex, jordan):
    assert alex.get("/api/insurer/dashboard").status_code == 403
    assert alex.get("/api/insurer/claims").status_code == 403
    assert jordan.get("/api/claims").status_code == 403
    assert jordan.get("/api/overview").status_code == 403
    assert jordan.get("/api/insurer/audit").status_code == 403  # handler, not admin


def test_customer_cannot_see_another_customers_records(alex, priya, db):
    priya_claim = db.execute(select(Claim).where(Claim.reference == "CLM-2026-100004")).scalar_one()
    assert alex.get(f"/api/claims/{priya_claim.id}").status_code == 404
    assert alex.get(f"/api/policies/{priya_claim.policy_id}").status_code == 404
    assert alex.post(f"/api/claims/{priya_claim.id}/submit").status_code == 404
    # agent tools are scoped too
    out = alex.chat("What's the status of my claim?")
    assert "CLM-2026-100004" not in out["message"]["content"]


def test_insurer_tenant_isolation(jordan, wanglei, db):
    pengcheng_claim = db.execute(select(Claim).where(Claim.reference == "CLM-2026-100003")).scalar_one()
    northgate_claim = db.execute(select(Claim).where(Claim.reference == "CLM-2026-100001")).scalar_one()
    assert jordan.get(f"/api/insurer/claims/{pengcheng_claim.id}").status_code == 404
    assert wanglei.get(f"/api/insurer/claims/{northgate_claim.id}").status_code == 404
    assert jordan.post(f"/api/insurer/claims/{pengcheng_claim.id}/assign", json={"handler_id": "me"}).status_code == 404
    refs = {c["reference"] for c in jordan.get("/api/insurer/claims?page_size=100").json()["claims"]}
    assert "CLM-2026-100003" not in refs and "CLM-2026-100001" in refs
    refs2 = {c["reference"] for c in wanglei.get("/api/insurer/claims?page_size=100").json()["claims"]}
    assert refs2 == {"CLM-2026-100003", "CLM-2026-100010"}


def test_pre_submission_claims_are_invisible_to_insurers(alex, jordan):
    home = next(p for p in alex.get("/api/policies").json()["policies"] if p["policy_number"] == "NGM-HC-204481")
    r = alex.post("/api/claims", json={"policy_id": home["id"], "incident_type": "storm", "description": "Roof tiles blown off in the storm last night", "location": "Home", "incident_date": "2026-10-10"})
    assert r.status_code == 200
    claim_id = r.json()["claim"]["id"]
    assert jordan.get(f"/api/insurer/claims/{claim_id}").status_code == 404
    assert claim_id not in {c["id"] for c in jordan.get("/api/insurer/claims?page_size=100").json()["claims"]}


def test_approval_requires_valid_single_use_token(alex, db):
    r = alex.chat("Draft an email asking my insurer for an update on my water leak claim")
    comm = next(b for b in r["message"]["blocks"] if b["type"] == "communication_draft")["communication"]
    r2 = alex.post(f"/api/communications/{comm['id']}/send")
    approval = r2.json()["approval"]
    assert alex.post(f"/api/approvals/{approval['id']}/approve", json={"token": "wrong-token-value"}).status_code == 400
    ok = alex.post(f"/api/approvals/{approval['id']}/approve", json={"token": approval["token"]})
    assert ok.status_code == 200 and ok.json()["approval"]["status"] == "consumed"
    again = alex.post(f"/api/approvals/{approval['id']}/approve", json={"token": approval["token"]})
    assert again.status_code == 400  # not reusable


def test_other_customer_cannot_approve(alex, priya):
    r = alex.chat("Draft an email asking my insurer for an update")
    comm = next(b for b in r["message"]["blocks"] if b["type"] == "communication_draft")["communication"]
    approval = alex.post(f"/api/communications/{comm['id']}/send").json()["approval"]
    assert priya.post(f"/api/approvals/{approval['id']}/approve", json={"token": approval["token"]}).status_code == 400


def test_assistant_rate_limit(alex, monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_assistant_per_minute", 2)
    conv = alex.post("/api/assistant/conversations", json={}).json()["conversation"]["id"]
    assert alex.post(f"/api/assistant/conversations/{conv}/messages", json={"text": "hello"}).status_code == 200
    assert alex.post(f"/api/assistant/conversations/{conv}/messages", json={"text": "hello"}).status_code == 200
    assert alex.post(f"/api/assistant/conversations/{conv}/messages", json={"text": "hello"}).status_code == 429


def test_security_headers_and_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["audit_chain_ok"] is True
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"


def test_dev_demo_accounts_only_in_dev(client):
    r = client.get("/api/dev/demo-accounts")
    assert r.status_code == 200 and any(a["email"] == "alex.tan@demo.example" for a in r.json()["accounts"])
