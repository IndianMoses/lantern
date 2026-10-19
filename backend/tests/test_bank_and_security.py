"""Bank integration surface, consent, record sharing and account security."""
from __future__ import annotations

import time

from sqlalchemy import select

from app.config import settings
from app.models import ConsentRecord, Roles, User
from app.security import limiter, totp_at, verify_totp
from app.services import bank as bank_svc


def test_bank_admin_sees_aggregates_only(login, alex, client):
    dana = login("dana.whitfield@harbourline.example", "dana-demo-2026")
    r = dana.get("/api/bank/overview")
    assert r.status_code == 200, r.text
    body = r.json()
    m = body["metrics"]
    assert m["customers_total"]["value"] >= 5 and m["customers_counted"]["value"] >= 3
    assert "definition" in m["coverage_analysis_completion"]
    text = r.text.lower()
    for forbidden in ("alex.tan", "priya", "clm-2026", "biscuit", "@demo.example"):
        assert forbidden not in text
    # no route to identifiable customer records
    assert dana.get(f"/api/bank/customers/{alex.user['id']}").status_code == 403
    assert dana.get("/api/insurer/claims").status_code == 403
    assert dana.get("/api/claims").status_code == 403
    assert alex.get("/api/bank/overview").status_code == 403
    integ = dana.get("/api/bank/integration").json()
    assert len(integ["interfaces"]) == 8 and all(i["mode"] == "mock" and i["status"] == "mock" for i in integ["interfaces"])
    assert "transactions" in integ["data_boundary"]["never_ingested"]
    aud = dana.get("/api/bank/audit").json()
    assert aud["chain"]["ok"] and any(e["action"] == "bank.analytics_viewed" for e in aud["events"])


def test_analytics_respect_consent(login, db):
    dana = login("dana.whitfield@harbourline.example", "dana-demo-2026")
    before = dana.get("/api/bank/overview").json()["metrics"]["customers_counted"]["value"]
    omar = login("omar.haddad@demo.example", "omar-demo-2026")
    r = omar.post("/api/consents", json={"purpose": "analytics_aggregate", "granted": False})
    assert r.status_code == 200
    after = dana.get("/api/bank/overview").json()["metrics"]["customers_counted"]["value"]
    assert after == before - 1
    # locked purposes cannot be withdrawn through the toggle
    assert omar.post("/api/consents", json={"purpose": "insurance_workflows", "granted": False}).status_code == 400
    rows = db.execute(select(ConsentRecord).where(ConsentRecord.user_id == omar.user["id"], ConsentRecord.purpose == "analytics_aggregate")).scalars().all()
    assert rows and rows[-1].granted is False  # append only trail
    omar.post("/api/consents", json={"purpose": "analytics_aggregate", "granted": True})


def test_consent_gates_external_model_processing(alex, db, monkeypatch):
    from app.agent import runner

    class FakeProvider:
        name = "anthropic"
        model = "fake"

        def complete(self, **kw):
            raise AssertionError("external provider must not be called without consent")

    monkeypatch.setattr(runner, "get_provider", lambda: FakeProvider())
    alex.post("/api/consents", json={"purpose": "external_model_processing", "granted": False})
    out = alex.chat("Is my bike covered at work?")
    assert out["run"]["provider"] == "deterministic"
    assert any(p.get("step") == "consent" for p in out["run"]["plan"])
    alex.post("/api/consents", json={"purpose": "external_model_processing", "granted": True})


def test_record_sharing_is_explicit_and_revocable(alex, priya, client, login, db):
    home = next(p for p in alex.get("/api/policies").json()["policies"] if p["policy_number"] == "NGM-HC-204481")
    assert priya.get(f"/api/policies/{home['id']}").status_code == 404
    r = alex.post("/api/shares", json={"grantee_email": "priya.nair@demo.example", "record_type": "policy", "record_id": home["id"], "permissions": ["view"]})
    assert r.status_code == 200, r.text
    share_id = r.json()["share"]["id"]
    assert priya.get(f"/api/policies/{home['id']}").status_code == 200
    # view only: no edits, no claims against it
    assert priya.patch(f"/api/policies/{home['id']}", json={"product_name": "x"}).status_code == 404
    assert priya.get("/api/shares").json()["received"][0]["record_label"] == "NGM-HC-204481"
    assert alex.delete(f"/api/shares/{share_id}").status_code == 200
    assert priya.get(f"/api/policies/{home['id']}").status_code == 404
    # a household member account can only see what was shared
    r = client.post("/api/auth/register", json={"email": "partner@example.com", "password": "a-strong-passphrase-2026", "full_name": "Sam Tan", "locale": "en", "country": "GB"})
    assert r.status_code == 200
    u = db.execute(select(User).where(User.email == "partner@example.com")).scalar_one()
    u.role = Roles.HOUSEHOLD_MEMBER
    db.commit()
    partner = login("partner@example.com", "a-strong-passphrase-2026")
    assert partner.get("/api/policies").status_code == 403  # no portfolio of their own on this surface
    alex.post("/api/shares", json={"grantee_email": "partner@example.com", "record_type": "policy", "record_id": home["id"]})
    assert partner.get(f"/api/policies/{home['id']}").status_code == 200


def test_sessions_list_and_revoke(login):
    a1 = login("alex.tan@demo.example", "alex-demo-2026")
    a2 = login("alex.tan@demo.example", "alex-demo-2026")
    sessions = a1.get("/api/auth/sessions").json()["sessions"]
    assert len(sessions) >= 2 and sum(1 for s in sessions if s["current"]) == 1
    r = a1.post("/api/auth/sessions/revoke", json={"all_others": True})
    assert r.json()["revoked"] >= 1
    assert a2.get("/api/auth/me").status_code == 401
    assert a1.get("/api/auth/me").status_code == 200


def test_change_password_revokes_other_sessions(login):
    a1 = login("chloe.bennett@demo.example", "chloe-demo-2026")
    a2 = login("chloe.bennett@demo.example", "chloe-demo-2026")
    assert a1.post("/api/auth/change-password", json={"current_password": "wrong-password-1", "new_password": "a-new-strong-passphrase-1"}).status_code == 401
    r = a1.post("/api/auth/change-password", json={"current_password": "chloe-demo-2026", "new_password": "a-new-strong-passphrase-1"})
    assert r.status_code == 200
    assert a2.get("/api/auth/me").status_code == 401
    login("chloe.bennett@demo.example", "a-new-strong-passphrase-1")
    a3 = login("chloe.bennett@demo.example", "a-new-strong-passphrase-1")
    a3.post("/api/auth/change-password", json={"current_password": "a-new-strong-passphrase-1", "new_password": "chloe-demo-2026"})


def test_lockout_after_repeated_failures(client):
    limiter.reset()
    email = "sofia.rossi@demo.example"
    for _ in range(settings.lockout_threshold):
        r = client.post("/api/auth/login", json={"email": email, "password": "wrong-password-1"})
        assert r.status_code == 401
        limiter.reset()
    r = client.post("/api/auth/login", json={"email": email, "password": "sofia-demo-2026"})
    assert r.status_code == 423
    from app.models import LoginAttempt

    from app.db import SessionLocal

    db = SessionLocal()
    db.delete(db.get(LoginAttempt, email))
    db.commit()
    db.close()
    limiter.reset()
    assert client.post("/api/auth/login", json={"email": email, "password": "sofia-demo-2026"}).status_code == 200


def test_password_reset_flow(client):
    from app.routers import auth as auth_router

    limiter.reset()
    r = client.post("/api/auth/reset/request", json={"email": "dev.patel@demo.example"})
    assert r.status_code == 200 and "If the address is registered" in r.json()["detail"]
    # an unknown address gets the same answer (no account enumeration)
    assert client.post("/api/auth/reset/request", json={"email": "nobody@example.com"}).json()["detail"] == r.json()["detail"]
    token = auth_router._dev_reset_tokens["dev.patel@demo.example"]
    assert client.post("/api/auth/reset/confirm", json={"token": "not-a-real-token-value", "new_password": "another-strong-passphrase-1"}).status_code == 400
    assert client.post("/api/auth/reset/confirm", json={"token": token, "new_password": "another-strong-passphrase-1"}).status_code == 200
    assert client.post("/api/auth/reset/confirm", json={"token": token, "new_password": "another-strong-passphrase-2"}).status_code == 400  # single use
    limiter.reset()
    assert client.post("/api/auth/login", json={"email": "dev.patel@demo.example", "password": "another-strong-passphrase-1"}).status_code == 200


def test_totp_mfa_enrolment_and_login(login, client):
    morgan = login("morgan.clarke@northgate.example", "morgan-demo-2026")
    setup = morgan.post("/api/auth/mfa/setup").json()
    secret = setup["secret"]
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    assert morgan.post("/api/auth/mfa/enable", json={"code": "000000"}).status_code == 400 or not verify_totp(secret, "000000")
    code = totp_at(secret, int(time.time() // 30))
    r = morgan.post("/api/auth/mfa/enable", json={"code": code})
    assert r.status_code == 200 and r.json()["mfa_enabled"] is True
    limiter.reset()
    r = client.post("/api/auth/login", json={"email": "morgan.clarke@northgate.example", "password": "morgan-demo-2026"})
    assert r.status_code == 200 and r.json()["mfa_required"] is True
    challenge = r.json()["challenge_token"]
    bad = client.post("/api/auth/mfa/verify", json={"challenge_token": challenge, "code": "123456"})
    assert bad.status_code == 401
    good = client.post("/api/auth/mfa/verify", json={"challenge_token": challenge, "code": totp_at(secret, int(time.time() // 30))})
    assert good.status_code == 200 and good.json()["user"]["mfa_passed"] is True
    # clean up: disable for the other tests
    csrf = good.json()["csrf_token"]
    r = client.post("/api/auth/mfa/disable", json={"code": totp_at(secret, int(time.time() // 30))}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200


def test_mfa_required_for_privileged_when_configured(login, monkeypatch):
    monkeypatch.setattr(settings, "mfa_required_for_privileged", True)
    morgan = login("morgan.clarke@northgate.example", "morgan-demo-2026")
    assert morgan.get("/api/insurer/audit").status_code == 403
    dana = login("dana.whitfield@harbourline.example", "dana-demo-2026")
    assert dana.get("/api/bank/overview").status_code == 403
    jordan = login("jordan.reyes@northgate.example", "jordan-demo-2026")
    assert jordan.get("/api/insurer/claims").status_code == 200  # handlers are not in the privileged set


def test_security_headers_include_csp(client):
    r = client.get("/api/health")
    assert "default-src 'self'" in r.headers.get("content-security-policy", "")
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("cache-control") == "no-store"


def test_feedback_recorded_for_own_messages_only(alex, priya):
    out = alex.chat("Is my bike covered at work?")
    mid = out["message"]["id"]
    assert alex.post("/api/feedback", json={"message_id": mid, "rating": 1}).status_code == 200
    assert priya.post("/api/feedback", json={"message_id": mid, "rating": -1}).status_code == 404
    assert alex.post("/api/feedback", json={"message_id": mid, "rating": 0}).status_code == 400


def test_mock_adapters_are_labelled(db):
    a = bank_svc.adapters_for(None)
    d = a.send("ref", "subject", "body")
    assert d.mock is True and d.accepted is True
    prof = a.fetch_profile("ref", purpose="bank_profile_import")
    assert prof.mock is True and "balances" not in prof.fields
    health = bank_svc.integration_health(None)
    assert all(h["mode"] == "mock" for h in health) and any(h["calls"] > 0 for h in health)
