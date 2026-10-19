"""Authentication: login with lockout and optional TOTP, registration,
sessions (list and revoke), password change and reset, MFA enrolment.

Every state change is audit logged. Reset tokens and MFA challenge tokens are
stored hashed and expire; nothing secret is returned except the reset token
to the mail channel (in development the dev router can surface it for the
demonstration, never in production).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import get_db
from ..deps import Principal, get_principal
from ..models import CustomerProfile, Organization, Roles, User, UserSession, utcnow
from ..security import (
    challenge_matches,
    consume_reset_token,
    create_reset_token,
    create_session,
    destroy_session,
    hash_password,
    limiter,
    lockout_status,
    new_challenge_token,
    new_totp_secret,
    otpauth_uri,
    record_login_failure,
    record_login_success,
    revoke_sessions,
    verify_password,
    verify_totp,
)
from ..services import bank as bank_svc

router = APIRouter(prefix="/api/auth", tags=["auth"])

# MFA challenges live for two minutes, in memory (one process); hashed token -> (user_id, expires)
_challenges: dict[str, tuple[str, float]] = {}


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    full_name: str = Field(min_length=2, max_length=120)
    locale: str = Field("en", pattern="^(en|zh)$")
    country: str = Field("GB", min_length=2, max_length=2)


def _user_payload(user: User, csrf: str, db: Session, session: UserSession | None = None, *, mfa_passed: bool | None = None) -> dict:
    org = db.get(Organization, user.org_id) if user.org_id else None
    passed = mfa_passed if mfa_passed is not None else (bool(session.mfa_passed) if session else False)
    return {
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "locale": user.locale, "org": ({"id": org.id, "code": org.code, "name": org.name, "name_zh": org.name_zh, "kind": org.kind} if org else None), "is_demo": user.is_demo, "mfa_enabled": bool(user.mfa_enabled), "mfa_passed": passed, "tier": Roles.TIER.get(user.role)},
        "csrf_token": csrf,
    }


def _set_cookie(response: Response, raw: str) -> None:
    response.set_cookie(settings.cookie_name, raw, httponly=True, samesite="lax", secure=settings.cookie_secure, max_age=settings.session_ttl_hours * 3600, path="/")


def _client(request: Request) -> tuple[str, str]:
    return (request.client.host if request.client else "unknown"), (request.headers.get("user-agent") or "")[:200]


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip, ua = _client(request)
    if not limiter.allow(f"login:{ip}", settings.rate_limit_login_per_minute):
        raise HTTPException(status_code=429, detail="Too many login attempts; try again in a minute")
    locked, remaining = lockout_status(db, body.email)
    if locked:
        audit.record(db, actor_type="anonymous", actor_id=None, action="auth.login", outcome="locked", details={"email": body.email, "ip": ip})
        raise HTTPException(status_code=423, detail=f"This account is temporarily locked after repeated failed attempts. Try again in {max(1, remaining // 60)} minute(s).")
    user = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash) or not user.is_active:
        now_locked = record_login_failure(db, body.email)
        audit.record(db, actor_type="anonymous", actor_id=None, action="auth.login", outcome="denied", details={"email": body.email, "ip": ip, "locked": now_locked})
        raise HTTPException(status_code=401, detail="Invalid email or password")
    record_login_success(db, body.email)
    if user.mfa_enabled and user.mfa_secret:
        raw, h = new_challenge_token()
        _challenges[h] = (user.id, time.time() + 120)
        audit.record(db, actor_type=user.role, actor_id=user.id, action="auth.mfa_challenge", details={"ip": ip})
        return {"mfa_required": True, "challenge_token": raw, "user": {"email": user.email, "full_name": user.full_name}}
    raw, csrf = create_session(db, user, ip=ip, user_agent=ua, mfa_passed=False)
    _set_cookie(response, raw)
    audit.record(db, actor_type=user.role, actor_id=user.id, action="auth.login", details={"ip": ip})
    return _user_payload(user, csrf, db)


class MfaVerifyIn(BaseModel):
    challenge_token: str
    code: str = Field(min_length=6, max_length=8)


@router.post("/mfa/verify")
def mfa_verify(body: MfaVerifyIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip, ua = _client(request)
    if not limiter.allow(f"mfa:{ip}", settings.rate_limit_login_per_minute):
        raise HTTPException(status_code=429, detail="Too many attempts")
    for h, (uid, exp) in list(_challenges.items()):
        if exp < time.time():
            _challenges.pop(h, None)
    hit = next(((h, uid) for h, (uid, _exp) in _challenges.items() if challenge_matches(h, body.challenge_token)), None)
    if not hit:
        raise HTTPException(status_code=401, detail="Challenge expired; sign in again")
    h, uid = hit
    user = db.get(User, uid)
    if not user or not verify_totp(user.mfa_secret or "", body.code):
        audit.record(db, actor_type=user.role if user else "anonymous", actor_id=uid, action="auth.mfa_verify", outcome="denied", details={"ip": ip})
        raise HTTPException(status_code=401, detail="Incorrect code")
    _challenges.pop(h, None)
    raw, csrf = create_session(db, user, ip=ip, user_agent=ua, mfa_passed=True)
    _set_cookie(response, raw)
    audit.record(db, actor_type=user.role, actor_id=user.id, action="auth.login", details={"ip": ip, "mfa": True})
    return _user_payload(user, csrf, db, mfa_passed=True)


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip, ua = _client(request)
    if not limiter.allow(f"register:{ip}", settings.rate_limit_login_per_minute):
        raise HTTPException(status_code=429, detail="Too many attempts; try again in a minute")
    if db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password), full_name=body.full_name.strip(), role=Roles.CUSTOMER, locale=body.locale, password_changed_at=utcnow())
    db.add(user)
    db.flush()
    db.add(CustomerProfile(user_id=user.id, country=body.country.upper(), currency="CNY" if body.country.upper() == "CN" else "GBP", preferences={"language": body.locale}, consents={}))
    bank_svc.ensure_default_consents(db, user)
    db.commit()
    raw, csrf = create_session(db, user, ip=ip, user_agent=ua)
    _set_cookie(response, raw)
    audit.record(db, actor_type="customer", actor_id=user.id, action="auth.register", details={"ip": ip})
    return _user_payload(user, csrf, db)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(settings.cookie_name)
    destroy_session(db, raw)
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _user_payload(p.user, p.session.csrf_token, db, p.session)


class LocaleIn(BaseModel):
    locale: str = Field(pattern="^(en|zh)$")


@router.patch("/me")
def update_me(body: LocaleIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.user.locale = body.locale
    db.commit()
    return _user_payload(p.user, p.session.csrf_token, db, p.session)


# ----------------------------------------------------------------- sessions ---


@router.get("/sessions")
def sessions(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(select(UserSession).where(UserSession.user_id == p.id).order_by(UserSession.last_seen_at.desc())).scalars().all()
    return {"sessions": [{"id": s.id[:12], "current": s.id == p.session.id, "ip": s.ip, "user_agent": s.user_agent, "created_at": s.created_at.isoformat(), "last_seen_at": s.last_seen_at.isoformat(), "expires_at": s.expires_at.isoformat(), "mfa_passed": bool(s.mfa_passed)} for s in rows]}


class RevokeIn(BaseModel):
    session_id: str | None = None  # 12 character prefix as listed
    all_others: bool = False


@router.post("/sessions/revoke")
def revoke(body: RevokeIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    n = 0
    if body.all_others:
        n = revoke_sessions(db, p.user, except_session_id=p.session.id)
    elif body.session_id:
        for s in db.execute(select(UserSession).where(UserSession.user_id == p.id)).scalars():
            if s.id.startswith(body.session_id) and s.id != p.session.id:
                db.delete(s)
                n += 1
        db.commit()
    audit.record(db, actor_type=p.role, actor_id=p.id, action="auth.sessions_revoked", details={"count": n, "all_others": body.all_others})
    return {"revoked": n}


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=8, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, p.user.password_hash):
        audit.record(db, actor_type=p.role, actor_id=p.id, action="auth.password_change", outcome="denied")
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="Choose a different password")
    p.user.password_hash = hash_password(body.new_password)
    p.user.password_changed_at = utcnow()
    db.commit()
    revoked = revoke_sessions(db, p.user, except_session_id=p.session.id)
    audit.record(db, actor_type=p.role, actor_id=p.id, action="auth.password_change", details={"sessions_revoked": revoked})
    return {"ok": True, "sessions_revoked": revoked}


# ----------------------------------------------------------- password reset ---


class ResetRequestIn(BaseModel):
    email: EmailStr


@router.post("/reset/request")
def reset_request(body: ResetRequestIn, request: Request, db: Session = Depends(get_db)):
    ip, _ = _client(request)
    if not limiter.allow(f"reset:{ip}", settings.rate_limit_login_per_minute):
        raise HTTPException(status_code=429, detail="Too many attempts")
    user = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if user and user.is_active:
        token = create_reset_token(db, user)
        # The token would go to the customer by the bank's approved channel; it is never returned here.
        bank_svc.adapters_for(None).send(user.id, "Password reset", f"Reset link token (mock delivery): {token[:6]}…")
        if settings.is_dev:
            _dev_reset_tokens[user.email] = token
    audit.record(db, actor_type="anonymous", actor_id=None, action="auth.password_reset_requested", details={"email": body.email, "ip": ip})
    return {"ok": True, "detail": "If the address is registered, reset instructions have been sent."}


_dev_reset_tokens: dict[str, str] = {}


class ResetConfirmIn(BaseModel):
    token: str = Field(min_length=10)
    new_password: str = Field(min_length=10, max_length=200)


@router.post("/reset/confirm")
def reset_confirm(body: ResetConfirmIn, request: Request, db: Session = Depends(get_db)):
    ip, _ = _client(request)
    if not limiter.allow(f"reset-confirm:{ip}", settings.rate_limit_login_per_minute):
        raise HTTPException(status_code=429, detail="Too many attempts")
    user = consume_reset_token(db, body.token)
    if not user:
        audit.record(db, actor_type="anonymous", actor_id=None, action="auth.password_reset", outcome="denied", details={"ip": ip})
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired")
    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = utcnow()
    db.commit()
    revoke_sessions(db, user)
    audit.record(db, actor_type=user.role, actor_id=user.id, action="auth.password_reset", details={"ip": ip})
    return {"ok": True}


# -------------------------------------------------------------------- MFA ---


@router.post("/mfa/setup")
def mfa_setup(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if p.user.mfa_enabled:
        raise HTTPException(status_code=409, detail="MFA is already enabled")
    secret = new_totp_secret()
    p.user.mfa_secret = secret  # pending until a code is verified
    db.commit()
    return {"secret": secret, "otpauth_uri": otpauth_uri(secret, p.user.email), "note": "Add this key to an authenticator app, then confirm with a code to enable."}


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=8)


@router.post("/mfa/enable")
def mfa_enable(body: MfaCodeIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.user.mfa_secret or not verify_totp(p.user.mfa_secret, body.code):
        raise HTTPException(status_code=400, detail="Incorrect code; MFA not enabled")
    p.user.mfa_enabled = True
    p.session.mfa_passed = True
    db.commit()
    audit.record(db, actor_type=p.role, actor_id=p.id, action="auth.mfa_enabled")
    return {"ok": True, "mfa_enabled": True}


@router.post("/mfa/disable")
def mfa_disable(body: MfaCodeIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if not p.user.mfa_enabled or not verify_totp(p.user.mfa_secret or "", body.code):
        raise HTTPException(status_code=400, detail="Incorrect code")
    if p.role in Roles.PRIVILEGED and settings.mfa_required_for_privileged:
        raise HTTPException(status_code=403, detail="MFA is required for this role")
    p.user.mfa_enabled = False
    p.user.mfa_secret = None
    db.commit()
    audit.record(db, actor_type=p.role, actor_id=p.id, action="auth.mfa_disabled")
    return {"ok": True, "mfa_enabled": False}
