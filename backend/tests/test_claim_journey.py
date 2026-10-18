"""End to end: a new customer joins, uploads a policy, finds gaps, reports an
incident, builds and approves the pack, the insurer handles it, the customer
challenges the decision. Also the failure and duplicate paths."""
from __future__ import annotations

import io
from pathlib import Path

from app.seed.files import make_png


def _png_bytes() -> bytes:
    p = Path("/tmp/lantern-test-photo.png")
    make_png(p)
    return p.read_bytes()


def test_full_claim_journey(client, login, jordan, tmp_root):
    # 1. a new customer registers
    r = client.post("/api/auth/register", json={"email": "new.customer@example.com", "password": "a-strong-passphrase-2026", "full_name": "New Customer", "locale": "en", "country": "GB"})
    assert r.status_code == 200
    cust = login("new.customer@example.com", "a-strong-passphrase-2026")

    # 2. onboarding profile
    r = cust.put("/api/profile", json={"country": "GB", "city": "Coventry", "currency": "GBP", "housing": "renter", "household": {"adults": 1}, "assets": [{"type": "bicycle", "label": "Commuter bike", "value": 900}, {"type": "laptop", "label": "Laptop used in cafés", "value": 1200}, {"type": "contents", "label": "Contents", "value": 12000}], "activities": [{"type": "hybrid_work", "label": "Works from cafés"}], "concerns": [], "preferences": {"language": "en"}, "consents": {"analytics": False}})
    assert r.status_code == 200

    # 3. uploads the fictional RentSure wording and gets a source grounded structure back
    wording = tmp_root / "uploads" / "demo" / "wordings" / "rentsure.pdf"
    r = cust.post("/api/policies/upload", files={"file": ("rentsure.pdf", wording.read_bytes(), "application/pdf")}, data={"insurance_type": "renters", "provider_name": "Northgate Mutual", "product_name": "RentSure Tenants Contents", "policy_number": "NGM-TC-999001", "start_date": "2026-01-01", "end_date": "2026-12-31"})
    assert r.status_code == 200, r.text
    uploaded = r.json()
    assert uploaded["extraction"]["clauses_found"] >= 8 and uploaded["policy"]["status"] == "pending_review"
    policy_id = uploaded["policy"]["id"]
    r = cust.patch(f"/api/policies/{policy_id}", json={"confirm": True})
    assert r.json()["policy"]["status"] == "active"
    detail = cust.get(f"/api/policies/{policy_id}").json()
    assert any(c["kind"] == "exclusion" for c in detail["clauses"]) and all(c["confidence"] < 1 for c in detail["clauses"])

    # 4. source grounded answer from the uploaded wording
    out = cust.chat("Does my policy cover theft from my room if there was no forced entry?")
    assert out["run"]["grounding"]["citations_valid"] >= 1 and "NGM-TC-999001" in out["message"]["content"]

    # 5. gap analysis sees the uploaded policy
    gaps = cust.post("/api/gaps/analyze").json()
    assert "NGM-TC-999001" in gaps["summary"]["policies_considered"]
    assert any(f["need"] == "laptop_away" for f in gaps["findings"])

    # 6. incident reported through the assistant -> draft claim with checklist
    out = cust.chat("My commuter bike was stolen from the locked bike store at my flat last night. I reported it to the police this morning. It cost £900.")
    assert out["run"]["intent"] == "claim_start"
    card = next(b for b in out["message"]["blocks"] if b["type"] == "claim_card")["claim"]
    claim_id = card["id"]
    conv = out["conversation_id"]
    checklist = next(b for b in out["message"]["blocks"] if b["type"] == "checklist")
    missing = [i["key"] for i in checklist["items"] if i["status"] == "required" and not i["satisfied"]]
    assert "police_report" in missing
    detail = cust.get(f"/api/claims/{claim_id}").json()
    assert detail["claim"]["status"] in {"information_required", "draft"} and detail["completeness"]["ready"] is False

    # 7. assistant identifies missing information
    out = cust.chat("What documents do I still need to provide?", conv)
    assert "police" in out["message"]["content"].lower()

    # 8. customer fills gaps and uploads evidence
    r = cust.patch(f"/api/claims/{claim_id}", json={"fields": {"location": "Bike store, Flat 4", "authorities_involved": True, "authority_reference": "WM/2026/12345", "items_involved": [{"label": "Commuter bike", "value": 900}], "estimated_loss": 900}})
    assert r.status_code == 200
    for key, name, mime, data in [("police_report", "crime-ref.txt", "text/plain", b"West Midlands Police. Crime reference WM/2026/12345 for theft of pedal cycle."), ("proof_of_ownership", "receipt.txt", "text/plain", b"Receipt: Commuter bike GBP 900.00 paid by card ending 4417"), ("photos", "bike-store.png", "image/png", _png_bytes())]:
        r = cust.post("/api/documents/upload", files={"file": (name, data, mime)}, data={"doc_type": "other", "claim_id": claim_id, "checklist_item_key": key})
        assert r.status_code == 200, r.text
    detail = cust.get(f"/api/claims/{claim_id}").json()
    assert detail["completeness"]["ready"] is True and detail["claim"]["status"] == "ready_for_review"

    # 9. approval gated submission through the assistant
    out = cust.chat("submit the claim", conv)
    approval_block = next(b for b in out["message"]["blocks"] if b["type"] == "approval")["approval"]
    assert approval_block["action_type"] == "submit_claim" and "sent" not in out["message"]["content"].lower().split("nothing has been sent")[0]
    detail = cust.get(f"/api/claims/{claim_id}").json()
    assert detail["claim"]["status"] == "awaiting_customer_approval"
    assert jordan.get(f"/api/insurer/claims/{claim_id}").status_code == 404  # still invisible to the insurer
    r = cust.post(f"/api/approvals/{approval_block['id']}/approve", json={"token": approval_block["token"]})
    assert r.status_code == 200 and r.json()["approval"]["status"] == "consumed"
    result = r.json()["approval"]["result"]
    assert result["status"] == "succeeded" and result["mock"] is True and result["insurer_reference"]
    detail = cust.get(f"/api/claims/{claim_id}").json()
    assert detail["claim"]["status"] == "submitted" and detail["submissions"][0]["mock"] is True
    assert any(d["doc_type"] == "claim_pack" for d in detail["documents"])

    # 10. customer tracks status; insurer now sees the claim
    out = cust.chat("What's the status of my claim?", conv)
    assert "Submitted" in out["message"]["content"]
    q = jordan.get("/api/insurer/claims?status=submitted").json()
    assert claim_id in {c["id"] for c in q["claims"]}
    ws = jordan.get(f"/api/insurer/claims/{claim_id}").json()
    assert len(ws["documents"]) == 4 and ws["coverage_reading"]["verdict"].startswith("appears")

    # 11. handler assigns and requests more information
    assert jordan.post(f"/api/insurer/claims/{claim_id}/assign", json={"handler_id": "me"}).json()["status"] == "acknowledged"
    r = jordan.post(f"/api/insurer/claims/{claim_id}/request-information", json={"message": "Please send a photo of the lock and the bike store door, and confirm the bike was locked to the stand.", "requested_items": ["Photo of lock", "Confirmation bike was locked"], "due_date": "2026-11-05"})
    assert r.status_code == 200 and r.json()["status"] == "additional_information_requested"
    request_id = r.json()["request_id"]

    # 12. customer responds (approval gated) and the claim returns to review
    detail = cust.get(f"/api/claims/{claim_id}").json()
    assert detail["information_requests"][0]["status"] == "open"
    r = cust.post("/api/documents/upload", files={"file": ("lock.png", _png_bytes(), "image/png")}, data={"doc_type": "photo", "claim_id": claim_id})
    lock_doc = r.json()["document"]["id"]
    r = cust.post(f"/api/claims/{claim_id}/information-requests/{request_id}/respond", json={"response_text": "The bike was locked to the stand with a D lock; photo attached.", "document_ids": [lock_doc]})
    approval = r.json()["approval"]
    r = cust.post(f"/api/approvals/{approval['id']}/approve", json={"token": approval["token"]})
    assert r.json()["approval"]["result"]["claim_status"] == "under_review"
    ws = jordan.get(f"/api/insurer/claims/{claim_id}").json()
    assert ws["information_requests"][0]["status"] == "answered" and any(m["kind"] == "information_response" for m in ws["communications"])

    # 13. insurer copilot summarises (no decision making) and handler records a decision
    cp = jordan.post("/api/assistant/conversations", json={"context": {"claim_id": claim_id}, "surface": "insurer_copilot"}).json()["conversation"]["id"]
    r = jordan.post(f"/api/assistant/conversations/{cp}/messages", json={"text": "Summarise this claim"})
    assert r.status_code == 200 and "[customer statement]" in r.json()["message"]["content"]
    assert all(t["tier"] <= 1 for t in r.json()["run"]["tool_executions"])
    r = jordan.post(f"/api/insurer/claims/{claim_id}/decision", json={"outcome": "partially_approved", "reason": "Theft of the pedal cycle is covered under clause 2.5, limited to £350 for an unspecified cycle; £100 excess deducted under clause 5.1.", "clause_refs": ["2.5", "5.1"], "approved_amount": 250})
    assert r.status_code == 200 and r.json()["status"] == "partially_approved"

    # 14. dashboard reflects it
    dash = jordan.get("/api/insurer/dashboard").json()["metrics"]
    assert dash["decided"]["value"] >= 3 and dash["amounts"]["approved"]["value"] >= 250

    # 15. customer sees and challenges the decision
    detail = cust.get(f"/api/claims/{claim_id}").json()
    assert detail["decision"]["outcome"] == "partially_approved"
    out = cust.chat("Explain why my claim was only partly approved and help me ask for a review", conv)
    assert out["run"]["intent"] == "dispute" and out["run"]["grounding"]["citations_unverified"] == 0
    assert any(b["type"] == "communication_draft" for b in out["message"]["blocks"])
    assert "cannot tell you it will succeed" in out["message"]["content"]


def test_duplicate_submission_is_not_duplicated(alex, db):
    policy = next(p for p in alex.get("/api/policies").json()["policies"] if p["policy_number"] == "NGM-HC-204481")
    r = alex.post("/api/claims", json={"policy_id": policy["id"], "incident_type": "storm", "description": "Fence panels and the shed roof were torn off in high winds.", "location": "Garden", "incident_date": "2026-10-12", "estimated_loss": 600, "items_involved": [{"label": "Shed roof", "value": 400}, {"label": "Fence", "value": 200}]})
    claim_id = r.json()["claim"]["id"]
    alex.post("/api/documents/upload", files={"file": ("shed.png", _png_bytes(), "image/png")}, data={"doc_type": "photo", "claim_id": claim_id, "checklist_item_key": "photos"})
    alex.post("/api/documents/upload", files={"file": ("quote.txt", b"Repair quote: shed roof and fence GBP 580", "text/plain")}, data={"doc_type": "estimate", "claim_id": claim_id, "checklist_item_key": "repair_estimate"})
    alex.post("/api/documents/upload", files={"file": ("receipt.txt", b"Shed purchase receipt GBP 450", "text/plain")}, data={"doc_type": "receipt", "claim_id": claim_id, "checklist_item_key": "proof_of_ownership"})
    detail = alex.get(f"/api/claims/{claim_id}").json()
    assert detail["completeness"]["ready"], detail["completeness"]
    a1 = alex.post(f"/api/claims/{claim_id}/submit").json()["approval"]
    a2 = alex.post(f"/api/claims/{claim_id}/submit").json()["approval"]  # second preparation expires the first
    assert alex.post(f"/api/approvals/{a1['id']}/approve", json={"token": a1["token"]}).status_code == 400
    ok = alex.post(f"/api/approvals/{a2['id']}/approve", json={"token": a2["token"]})
    assert ok.json()["approval"]["result"]["status"] == "succeeded"
    assert alex.post(f"/api/approvals/{a2['id']}/approve", json={"token": a2["token"]}).status_code == 400
    assert alex.post(f"/api/claims/{claim_id}/submit").status_code == 400  # already submitted
    detail = alex.get(f"/api/claims/{claim_id}").json()
    assert len([s for s in detail["submissions"] if s["status"] == "succeeded"]) == 1


def test_adapter_failure_is_recorded_and_retry_works(alex):
    policy = next(p for p in alex.get("/api/policies").json()["policies"] if p["policy_number"] == "NGM-HC-204481")
    r = alex.post("/api/claims", json={"policy_id": policy["id"], "incident_type": "fire", "description": "Small kitchen fire SIMULATE_ADAPTER_FAILURE damaged the extractor hood.", "location": "Kitchen", "incident_date": "2026-10-13", "estimated_loss": 900, "items_involved": [{"label": "Extractor hood", "value": 900}]})
    claim_id = r.json()["claim"]["id"]
    alex.post("/api/documents/upload", files={"file": ("hood.png", _png_bytes(), "image/png")}, data={"doc_type": "photo", "claim_id": claim_id, "checklist_item_key": "photos"})
    alex.post("/api/documents/upload", files={"file": ("quote.txt", b"Replacement hood quote GBP 850", "text/plain")}, data={"doc_type": "estimate", "claim_id": claim_id, "checklist_item_key": "repair_estimate"})
    alex.post("/api/documents/upload", files={"file": ("receipt.txt", b"Hood purchase receipt GBP 700", "text/plain")}, data={"doc_type": "receipt", "claim_id": claim_id, "checklist_item_key": "proof_of_ownership"})
    a = alex.post(f"/api/claims/{claim_id}/submit").json()["approval"]
    r = alex.post(f"/api/approvals/{a['id']}/approve", json={"token": a["token"]}).json()["approval"]
    assert r["status"] == "consumed" and r["result"]["status"] == "failed" and r["result"]["claim_status"] == "submission_failed"
    detail = alex.get(f"/api/claims/{claim_id}").json()
    assert detail["claim"]["status"] == "submission_failed" and detail["submissions"][0]["status"] == "failed"
    assert any(e["event_type"] == "status_changed" and "failed" in e["summary"].lower() for e in detail["timeline"])
    # the customer fixes the description (removing the failure trigger) and retries
    alex.patch(f"/api/claims/{claim_id}", json={"fields": {"description": "Small kitchen fire damaged the extractor hood."}})
    a2 = alex.post(f"/api/claims/{claim_id}/submit").json()["approval"]
    r2 = alex.post(f"/api/approvals/{a2['id']}/approve", json={"token": a2["token"]}).json()["approval"]
    assert r2["result"]["status"] == "succeeded"


def test_assisted_mode_pack_pdf(alex):
    claims = alex.get("/api/claims").json()["claims"]
    claim = next(c for c in claims if c["reference"] == "CLM-2026-100001")
    r = alex.post(f"/api/claims/{claim['id']}/pack/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    pack = alex.get(f"/api/claims/{claim['id']}/pack").json()["pack"]
    assert pack["claim_reference"] == "CLM-2026-100001" and pack["coverage_reading"]["note"].startswith("Customer side")
