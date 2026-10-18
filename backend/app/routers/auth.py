from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import get_db
from ..deps import Principal, get_principal
from ..models import CustomerProfile, Organization, Roles, User
from ..security import create_session, destroy_session, hash_password, limiter, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    full_name: str = Field(min_length=2, max_length=120)
    locale: str = Field("en", pattern="^(en|zh)$")
    country: str = Field("GB", min_length=2, max_length=2)


def _user_payload(user: User, csrf: str, db: Session) -> dict:
    org = db.get(Organization, user.org_id) if user.org_id else None
    return {
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "locale": user.locale, "org": ({"id": org.id, "code": org.code, "name": org.name, "name_zh": org.name_zh} if org else None), "is_demo": user.is_demo},
        "csrf_token": csrf,
    }


def _set_cookie(response: Response, raw: str) -> None:
    response.set_cookie(settings.cookie_name, raw, httponly=True, samesite="lax", secure=settings.cookie_secure, max_age=settings.session_ttl_hours * 3600, path="/")


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.allow(f"login:{ip}", settings.rate_limit_login_per_minute):
        raise HTTPException(status_code=429, detail="Too many login attempts; try again in a minute")
    user = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash) or not user.is_active:
        audit.record(db, actor_type="anonymous", actor_id=None, action="auth.login", outcome="denied", details={"email": body.email, "ip": ip})
        raise HTTPException(status_code=401, detail="Invalid email or password")
    raw, csrf = create_session(db, user, ip=ip)
    _set_cookie(response, raw)
    audit.record(db, actor_type=user.role, actor_id=user.id, action="auth.login", details={"ip": ip})
    return _user_payload(user, csrf, db)


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    if not limiter.allow(f"register:{ip}", settings.rate_limit_login_per_minute):
        raise HTTPException(status_code=429, detail="Too many attempts; try again in a minute")
    if db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(email=body.email.lower(), password_hash=hash_password(body.password), full_name=body.full_name.strip(), role=Roles.CUSTOMER, locale=body.locale)
    db.add(user)
    db.flush()
    db.add(CustomerProfile(user_id=user.id, country=body.country.upper(), currency="CNY" if body.country.upper() == "CN" else "GBP", preferences={"language": body.locale}, consents={}))
    db.commit()
    raw, csrf = create_session(db, user, ip=ip)
    _set_cookie(response, raw)
    audit.record(db, actor_type="customer", actor_id=user.id, action="auth.register", details={"ip": ip})
    return _user_payload(user, csrf, db)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    destroy_session(db, request.cookies.get(settings.cookie_name))
    response.delete_cookie(settings.cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    return _user_payload(p.user, p.session.csrf_token, db)


class LocaleIn(BaseModel):
    locale: str = Field(pattern="^(en|zh)$")


@router.patch("/me")
def update_me(body: LocaleIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    p.user.locale = body.locale
    db.commit()
    return _user_payload(p.user, p.session.csrf_token, db)
