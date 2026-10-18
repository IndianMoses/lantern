"""Human in the loop approvals.

Tier 2 actions (anything that leaves the platform or commits the customer)
are never executed by the model. A tool call only *prepares* an
ApprovalRequest; the customer sees the full payload and consequences in the
interface and approves with a one time token bound to that payload. Only
then does a backend service execute the action, exactly once.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..models import ApprovalRequest, Claim, ClaimEvent, Communication, InformationRequest, Notification, User, utcnow
from ..security import approval_token_matches, new_approval_token, payload_hash
from . import claims as claim_svc


class ApprovalError(ValueError):
    pass


def create_communication_approval(db: Session, user: User, comm: Communication, *, run_id: str | None = None, actor_type: str = "customer") -> claim_svc.ApprovalCreated:
    if comm.user_id != user.id:
        raise PermissionError("communication does not belong to this customer")
    if comm.status != "draft":
        raise ApprovalError("only drafts can be sent")
    claim = db.get(Claim, comm.claim_id) if comm.claim_id else None
    payload = {"communication_id": comm.id, "subject": comm.subject, "body": comm.body, "recipient": comm.recipient, "claim_reference": claim.reference if claim else None, "documents": comm.referenced_document_ids}
    raw, token_hash = new_approval_token()
    lang = (claim.language if claim else user.locale) or "en"
    approval = ApprovalRequest(
        user_id=user.id, run_id=run_id, action_type="send_communication", target_type="communication", target_id=comm.id, payload=payload, payload_hash=payload_hash(payload),
        summary=(f"Send the message '{comm.subject}' to {comm.recipient or 'the insurer'}." if lang != "zh" else f"向 {comm.recipient or '保险公司'} 发送消息“{comm.subject}”。"),
        consequences=[
            {"en": "The message text will be delivered to the insurer and recorded on the claim.", "zh": "消息内容将发送给保险公司并记录在理赔中。"},
            {"en": "Statements in the message may be relied on by the insurer; check every fact is correct.", "zh": "保险公司可能依赖消息中的陈述，请核对每一事实。"},
        ],
        status="pending", token_hash=token_hash, expires_at=utcnow() + timedelta(minutes=settings.approval_ttl_minutes),
    )
    db.add(approval)
    db.flush()
    comm.approval_id = approval.id
    audit.record(db, actor_type=actor_type, actor_id=user.id, action="approval.create", target_type="approval", target_id=approval.id, details={"action_type": "send_communication"}, commit=False)
    return claim_svc.ApprovalCreated(approval=approval, token=raw)


def create_information_response_approval(db: Session, user: User, claim: Claim, req: InformationRequest, response_text: str, document_ids: list[str], *, run_id: str | None = None, actor_type: str = "customer") -> claim_svc.ApprovalCreated:
    if claim.user_id != user.id or req.claim_id != claim.id:
        raise PermissionError("request does not belong to this customer")
    if req.status != "open":
        raise ApprovalError("this request has already been answered")
    payload = {"request_id": req.id, "claim_reference": claim.reference, "response_text": response_text, "documents": document_ids}
    raw, token_hash = new_approval_token()
    approval = ApprovalRequest(
        user_id=user.id, run_id=run_id, action_type="respond_information_request", target_type="information_request", target_id=req.id, payload=payload, payload_hash=payload_hash(payload),
        summary=(f"Send your response and {len(document_ids)} document(s) to the insurer for claim {claim.reference}." if (claim.language or "en") != "zh" else f"向保险公司发送你对理赔 {claim.reference} 的回复及 {len(document_ids)} 份文件。"),
        consequences=[{"en": "The response and documents become part of the claim file with the insurer.", "zh": "回复和文件将成为保险公司理赔档案的一部分。"}],
        status="pending", token_hash=token_hash, expires_at=utcnow() + timedelta(minutes=settings.approval_ttl_minutes),
    )
    db.add(approval)
    db.flush()
    audit.record(db, actor_type=actor_type, actor_id=user.id, action="approval.create", target_type="approval", target_id=approval.id, details={"action_type": "respond_information_request"}, commit=False)
    return claim_svc.ApprovalCreated(approval=approval, token=raw)


def get_owned(db: Session, user: User, approval_id: str) -> ApprovalRequest:
    approval = db.get(ApprovalRequest, approval_id)
    if not approval or approval.user_id != user.id:
        raise ApprovalError("approval not found")
    return approval


def decide(db: Session, user: User, approval_id: str, token: str | None, *, approve: bool) -> ApprovalRequest:
    approval = get_owned(db, user, approval_id)
    if approval.status != "pending":
        raise ApprovalError(f"approval is {approval.status}")
    if approval.expires_at < utcnow():
        approval.status = "expired"
        db.commit()
        raise ApprovalError("approval expired; ask the assistant to prepare it again")
    if not approval_token_matches(approval.token_hash, token):
        audit.record(db, actor_type="customer", actor_id=user.id, action="approval.decide", target_type="approval", target_id=approval.id, outcome="denied", details={"reason": "bad token"})
        raise ApprovalError("approval token invalid")
    approval.decided_at = utcnow()
    if not approve:
        approval.status = "rejected"
        _after_reject(db, approval, user)
        audit.record(db, actor_type="customer", actor_id=user.id, action="approval.reject", target_type="approval", target_id=approval.id, commit=False)
        db.commit()
        return approval
    approval.status = "approved"
    audit.record(db, actor_type="customer", actor_id=user.id, action="approval.approve", target_type="approval", target_id=approval.id, details={"action_type": approval.action_type}, commit=False)
    try:
        result = _execute(db, approval, user)
        approval.status = "consumed"
        approval.result = result
    except claim_svc.TransitionError as exc:
        approval.status = "failed"
        approval.result = {"error": str(exc)}
    db.commit()
    return approval


def _after_reject(db: Session, approval: ApprovalRequest, user: User) -> None:
    if approval.action_type == "submit_claim":
        claim = db.get(Claim, approval.target_id)
        if claim and claim.status == "awaiting_customer_approval":
            claim_svc.transition(db, claim, "ready_for_review", actor_type="customer", actor_id=user.id, summary="Customer declined to submit; back to review")


def _execute(db: Session, approval: ApprovalRequest, user: User) -> dict:
    if approval.action_type == "submit_claim":
        claim = db.get(Claim, approval.target_id)
        if not claim or claim.user_id != user.id:
            raise claim_svc.TransitionError("claim not found")
        sub = claim_svc.execute_submission(db, claim, approval, user)
        return {"submission_id": sub.id, "status": sub.status, "insurer_reference": claim.insurer_reference, "error": sub.error, "claim_status": claim.status, "mock": bool(sub.response_payload.get("mock"))}
    if approval.action_type == "send_communication":
        comm = db.get(Communication, approval.target_id)
        if not comm or comm.user_id != user.id:
            raise claim_svc.TransitionError("communication not found")
        if payload_hash({"communication_id": comm.id, "subject": comm.subject, "body": comm.body, "recipient": comm.recipient, "claim_reference": (db.get(Claim, comm.claim_id).reference if comm.claim_id else None), "documents": comm.referenced_document_ids}) != approval.payload_hash:
            raise claim_svc.TransitionError("the draft changed after approval was prepared")
        comm.status = "sent"
        comm.sent_at = utcnow()
        comm.sender_user_id = user.id
        if comm.claim_id:
            claim = db.get(Claim, comm.claim_id)
            claim_svc.add_event(db, claim, actor_type="customer", actor_id=user.id, event_type="message_sent", summary=f"Message sent to insurer: {comm.subject}", data={"communication_id": comm.id})
            claim_svc.notify_org_staff(db, claim.insurer_org_id, kind="message", title=f"Message from customer on {claim.reference}", body=comm.subject, link=f"/insurer/claims/{claim.id}", only_user_id=claim.assigned_handler_id)
        audit.record(db, actor_type="customer", actor_id=user.id, action="communication.send", target_type="communication", target_id=comm.id, commit=False)
        return {"communication_id": comm.id, "status": "sent", "sent_at": comm.sent_at.isoformat()}
    if approval.action_type == "respond_information_request":
        req = db.get(InformationRequest, approval.target_id)
        claim = db.get(Claim, req.claim_id) if req else None
        if not req or not claim or claim.user_id != user.id:
            raise claim_svc.TransitionError("request not found")
        claim_svc.respond_information_request(db, claim, user, req, approval.payload["response_text"], approval.payload.get("documents", []))
        return {"request_id": req.id, "status": "answered", "claim_status": claim.status}
    raise claim_svc.TransitionError(f"unknown action {approval.action_type}")


def serialize(a: ApprovalRequest, token: str | None = None) -> dict:
    d = {
        "id": a.id, "action_type": a.action_type, "target_type": a.target_type, "target_id": a.target_id, "summary": a.summary, "consequences": a.consequences,
        "payload": a.payload, "status": a.status, "expires_at": a.expires_at.isoformat(), "created_at": a.created_at.isoformat(), "decided_at": a.decided_at.isoformat() if a.decided_at else None, "result": a.result,
    }
    if token:
        d["token"] = token
    return d


def pending_for_user(db: Session, user: User) -> list[ApprovalRequest]:
    return list(db.execute(select(ApprovalRequest).where(ApprovalRequest.user_id == user.id, ApprovalRequest.status == "pending", ApprovalRequest.expires_at > utcnow()).order_by(ApprovalRequest.created_at.desc())).scalars())
