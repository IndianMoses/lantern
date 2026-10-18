from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import get_db
from ..deps import Principal, customer_claim_or_404, get_principal, require_customer
from ..models import Claim, ClaimStatus, Document, Roles
from ..services import claims as claim_svc
from ..services import policies as pol_svc
from ..services.extraction import ValidationError, extract_text, scan_for_injection, validate_upload

router = APIRouter(prefix="/api/documents", tags=["documents"])

DOC_TYPES = {"policy_wording", "receipt", "invoice", "photo", "estimate", "police_report", "correspondence", "medical", "vet_history", "booking", "claim_form", "device_identity", "proof_of_ownership", "other"}


def _safe_name(name: str) -> str:
    base = Path(name).name
    base = re.sub(r"[^\w.\-一-鿿 ]+", "_", base)[:120]
    return base or "upload"


def store_upload(db: Session, *, owner_id: str, uploader_id: str, upload: UploadFile, doc_type: str, claim_id: str | None = None, policy_id: str | None = None, item_key: str | None = None) -> Document:
    data = upload.file.read()
    try:
        mime = validate_upload(upload.filename or "upload", upload.content_type, data, settings.max_upload_bytes)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    sha = hashlib.sha256(data).hexdigest()
    folder = Path(settings.upload_dir) / owner_id
    folder.mkdir(parents=True, exist_ok=True)
    fname = _safe_name(upload.filename or "upload")
    path = folder / f"{uuid.uuid4().hex}_{fname}"
    path.write_bytes(data)
    text, meta = extract_text(mime, data)
    scan = scan_for_injection(text)
    doc = Document(
        owner_user_id=owner_id, uploaded_by_user_id=uploader_id, claim_id=claim_id, policy_id=policy_id, filename=fname, mime_type=mime, size_bytes=len(data), sha256=sha,
        storage_path=str(path), doc_type=doc_type if doc_type in DOC_TYPES else "other", checklist_item_key=item_key,
        extraction_status=("done" if text else ("not_applicable" if mime.startswith("image/") else "failed")), extracted_text=text, extraction_meta=meta, injection_flags=scan.flags,
    )
    db.add(doc)
    db.flush()
    audit.record(db, actor_type="customer", actor_id=uploader_id, action="document.upload", target_type="document", target_id=doc.id, details={"doc_type": doc.doc_type, "mime": mime, "size": len(data), "sha256": sha[:12], "injection_flags": len(scan.flags), "claim_id": claim_id}, commit=False)
    return doc


def doc_out(d: Document) -> dict:
    return {
        "id": d.id, "filename": d.filename, "mime_type": d.mime_type, "size_bytes": d.size_bytes, "sha256": d.sha256, "doc_type": d.doc_type, "claim_id": d.claim_id, "policy_id": d.policy_id,
        "checklist_item_key": d.checklist_item_key, "extraction_status": d.extraction_status, "extraction_meta": d.extraction_meta, "injection_flags": d.injection_flags,
        "has_text": bool(d.extracted_text), "submitted_at": d.submitted_at.isoformat() if d.submitted_at else None, "reviewed_by_insurer": d.reviewed_by_insurer, "created_at": d.created_at.isoformat(), "is_demo_data": d.is_demo_data,
    }


@router.get("")
def list_documents(p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    docs = db.execute(select(Document).where(Document.owner_user_id == p.id).order_by(Document.created_at.desc())).scalars().all()
    return {"documents": [doc_out(d) for d in docs]}


@router.post("/upload")
def upload_document(file: UploadFile = File(...), doc_type: str = Form("other"), claim_id: str | None = Form(None), checklist_item_key: str | None = Form(None), p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    claim = customer_claim_or_404(db, p, claim_id) if claim_id else None
    if claim and claim.status in {"settled", "closed"}:
        raise HTTPException(status_code=400, detail="This claim is closed")
    doc = store_upload(db, owner_id=p.id, uploader_id=p.id, upload=file, doc_type=doc_type, claim_id=None, item_key=checklist_item_key)
    if claim:
        claim_svc.attach_document(db, claim, doc, checklist_item_key, actor_type="customer", actor_id=p.id)
    db.commit()
    return {"document": doc_out(doc), "claim": (pol_svc.claim_summary(claim, p.user.locale) | {"checklist": claim.checklist, "completeness": claim.completeness}) if claim else None}


@router.get("/{document_id}")
def get_document(document_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = _authorised_document(db, p, document_id)
    out = doc_out(d)
    if p.role == Roles.CUSTOMER or d.claim_id:
        out["extracted_text"] = (d.extracted_text or "")[:20000]
    return {"document": out}


@router.get("/{document_id}/download")
def download_document(document_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    d = _authorised_document(db, p, document_id)
    path = Path(d.storage_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="File is no longer available")
    audit.record(db, actor_type=p.role, actor_id=p.id, action="document.download", target_type="document", target_id=d.id)
    return FileResponse(path, media_type=d.mime_type, filename=d.filename, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/{document_id}")
def delete_document(document_id: str, p: Principal = Depends(require_customer), db: Session = Depends(get_db)):
    d = db.get(Document, document_id)
    if not d or d.owner_user_id != p.id:
        raise HTTPException(status_code=404, detail="Not found")
    if d.submitted_at or d.doc_type in {"claim_pack"}:
        raise HTTPException(status_code=400, detail="Documents already shared with an insurer are retained; they cannot be deleted here")
    if d.policy_id:
        raise HTTPException(status_code=400, detail="Remove the policy instead; its wording document is kept with it")
    claim = db.get(Claim, d.claim_id) if d.claim_id else None
    try:
        Path(d.storage_path).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(d)
    db.flush()
    if claim:
        claim_svc.add_event(db, claim, actor_type="customer", actor_id=p.id, event_type="document_removed", summary=f"Removed {d.filename}", data={"document_id": d.id})
        claim_svc.refresh_claim_analysis(db, claim)
    audit.record(db, actor_type="customer", actor_id=p.id, action="document.delete", target_type="document", target_id=d.id, commit=False)
    db.commit()
    return {"ok": True}


def _authorised_document(db: Session, p: Principal, document_id: str) -> Document:
    d = db.get(Document, document_id)
    if not d:
        raise HTTPException(status_code=404, detail="Not found")
    if p.role == Roles.CUSTOMER and d.owner_user_id == p.id:
        return d
    if p.role in Roles.INSURER_ROLES and d.claim_id:
        claim = db.get(Claim, d.claim_id)
        if claim and claim.insurer_org_id == p.org_id and claim.status not in ClaimStatus.PRE_SUBMISSION and (d.submitted_at or d.doc_type == "claim_pack"):
            return d
    raise HTTPException(status_code=404, detail="Not found")
