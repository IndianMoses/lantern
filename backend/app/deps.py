"""FastAPI dependencies for authentication and authorisation.

Every sensitive endpoint uses one of these. Frontend visibility is never the
control; the server decides.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from sqlalchemy import select

from .models import Claim, ClaimStatus, Policy, RecordShare, Roles, User, UserSession
from .security import csrf_matches, resolve_session

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


SUPPORTED_LANGS = {"en", "zh"}


def resolve_lang(header: str | None, fallback: str | None) -> str:
    """The interface language for this request: the X-Lang header the client
    sends with every call (so labels follow the toggle immediately), falling
    back to the stored account preference."""
    if header:
        h = header.split(",")[0].strip().lower()[:2]
        if h in SUPPORTED_LANGS:
            return h
    return fallback if fallback in SUPPORTED_LANGS else "en"


@dataclass
class Principal:
    user: User
    session: UserSession
    lang: str = "en"

    @property
    def id(self) -> str:
        return self.user.id

    @property
    def role(self) -> str:
        return self.user.role

    @property
    def org_id(self) -> str | None:
        return self.user.org_id


def get_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    raw = request.cookies.get(settings.cookie_name)
    resolved = resolve_session(db, raw)
    if not resolved:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user, sess = resolved
    if request.method not in SAFE_METHODS and not csrf_matches(sess, request.headers.get("X-CSRF-Token")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid")
    return Principal(user=user, session=sess, lang=resolve_lang(request.headers.get("X-Lang") or request.headers.get("Accept-Language"), user.locale))


def require_customer(p: Principal = Depends(get_principal)) -> Principal:
    if p.role != Roles.CUSTOMER:
        raise HTTPException(status_code=403, detail="Customer access only")
    return p


def require_insurer(p: Principal = Depends(get_principal)) -> Principal:
    if p.role not in Roles.INSURER_ROLES or not p.org_id:
        raise HTTPException(status_code=403, detail="Insurer staff access only")
    return p


def require_insurer_admin(p: Principal = Depends(get_principal)) -> Principal:
    if p.role != Roles.INSURER_ADMIN or not p.org_id:
        raise HTTPException(status_code=403, detail="Insurer administrator access only")
    _require_mfa(p)
    return p


def require_bank_admin(p: Principal = Depends(get_principal)) -> Principal:
    if p.role != Roles.BANK_ADMIN or not p.org_id:
        raise HTTPException(status_code=403, detail="Bank administrator access only")
    _require_mfa(p)
    return p


def require_delegate_or_customer(p: Principal = Depends(get_principal)) -> Principal:
    """Customers, household members and advisers: read access to records that
    are theirs or explicitly shared with them (checked per record below)."""
    if p.role not in {Roles.CUSTOMER, Roles.HOUSEHOLD_MEMBER, Roles.ADVISER}:
        raise HTTPException(status_code=403, detail="Customer access only")
    return p


def _require_mfa(p: Principal) -> None:
    if settings.mfa_required_for_privileged and p.role in Roles.PRIVILEGED and not p.session.mfa_passed:
        raise HTTPException(status_code=403, detail="Multi factor authentication is required for this role; enrol under Settings")


def _shared(db: Session, p: Principal, record_type: str, record_id: str, permission: str) -> bool:
    if p.role not in {Roles.HOUSEHOLD_MEMBER, Roles.ADVISER, Roles.CUSTOMER}:
        return False
    row = db.execute(select(RecordShare).where(RecordShare.grantee_user_id == p.id, RecordShare.record_type == record_type, RecordShare.record_id == record_id, RecordShare.revoked_at.is_(None))).scalar_one_or_none()
    return bool(row and permission in (row.permissions or []))


# ------------------------------------------------------------ record scoping ---


def customer_claim_or_404(db: Session, p: Principal, claim_id: str, *, permission: str = "write") -> Claim:
    claim = db.get(Claim, claim_id)
    if not claim or (claim.user_id != p.id and not (permission == "view" and _shared(db, p, "claim", claim_id, "view"))):
        # 404 rather than 403 so that identifiers cannot be probed
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


def insurer_claim_or_404(db: Session, p: Principal, claim_id: str) -> Claim:
    claim = db.get(Claim, claim_id)
    if not claim or claim.insurer_org_id != p.org_id or claim.status in ClaimStatus.PRE_SUBMISSION:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


def customer_policy_or_404(db: Session, p: Principal, policy_id: str, *, permission: str = "write") -> Policy:
    policy = db.get(Policy, policy_id)
    if not policy or (policy.user_id != p.id and not (permission == "view" and _shared(db, p, "policy", policy_id, "view"))):
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy


def insurer_policy_or_404(db: Session, p: Principal, policy_id: str) -> Policy:
    policy = db.get(Policy, policy_id)
    if not policy or policy.insurer_org_id != p.org_id:
        raise HTTPException(status_code=404, detail="Policy not found")
    return policy
