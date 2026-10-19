"""Authentication primitives: password hashing, server side sessions,
CSRF double submit tokens, one time approval tokens and rate limiting."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from .config import settings
from .models import LoginAttempt, PasswordResetToken, User, UserSession, utcnow

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:  # malformed hash etc.
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ------------------------------------------------------------- sessions ---


def create_session(db: Session, user: User, ip: str | None = None, user_agent: str | None = None, *, mfa_passed: bool = False) -> tuple[str, str]:
    """Returns (raw_session_token, csrf_token). Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    sess = UserSession(
        id=_token_hash(raw),
        user_id=user.id,
        csrf_token=csrf,
        expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
        ip=ip,
        user_agent=(user_agent or "")[:200] or None,
        mfa_passed=mfa_passed,
    )
    db.add(sess)
    db.commit()
    return raw, csrf


def resolve_session(db: Session, raw_token: str | None) -> tuple[User, UserSession] | None:
    if not raw_token:
        return None
    sess = db.get(UserSession, _token_hash(raw_token))
    if not sess:
        return None
    if sess.expires_at < utcnow():
        db.delete(sess)
        db.commit()
        return None
    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        return None
    sess.last_seen_at = utcnow()
    db.commit()
    return user, sess


def destroy_session(db: Session, raw_token: str | None) -> None:
    if not raw_token:
        return
    sess = db.get(UserSession, _token_hash(raw_token))
    if sess:
        db.delete(sess)
        db.commit()


def csrf_matches(sess: UserSession, header_value: str | None) -> bool:
    return bool(header_value) and hmac.compare_digest(sess.csrf_token, header_value)


# ------------------------------------------------------------- approvals ---


def new_approval_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, _token_hash(raw)


def approval_token_matches(token_hash: str, raw_token: str | None) -> bool:
    return bool(raw_token) and hmac.compare_digest(token_hash, _token_hash(raw_token))


def payload_hash(payload: dict) -> str:
    import json

    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------- rate limiting ---


class SlidingWindowLimiter:
    """Small in memory limiter. Replace with Redis for multi process deployments."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._events[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


limiter = SlidingWindowLimiter()


# ------------------------------------------------------ account protection ---


def lockout_status(db: Session, email: str) -> tuple[bool, int]:
    """(locked, seconds_remaining)."""
    row = db.get(LoginAttempt, email.lower())
    if row and row.locked_until and row.locked_until > utcnow():
        return True, int((row.locked_until - utcnow()).total_seconds())
    return False, 0


def record_login_failure(db: Session, email: str) -> bool:
    """Returns True when this failure locked the account."""
    key = email.lower()
    row = db.get(LoginAttempt, key)
    if row is None:
        row = LoginAttempt(email=key, failures=0)
        db.add(row)
    row.failures += 1
    locked = False
    if row.failures >= settings.lockout_threshold:
        row.locked_until = utcnow() + timedelta(minutes=settings.lockout_minutes)
        row.failures = 0
        locked = True
    db.commit()
    return locked


def record_login_success(db: Session, email: str) -> None:
    row = db.get(LoginAttempt, email.lower())
    if row:
        db.delete(row)
        db.commit()


def revoke_sessions(db: Session, user: User, *, except_session_id: str | None = None) -> int:
    from sqlalchemy import select

    n = 0
    for s in db.execute(select(UserSession).where(UserSession.user_id == user.id)).scalars():
        if except_session_id and s.id == except_session_id:
            continue
        db.delete(s)
        n += 1
    db.commit()
    return n


def create_reset_token(db: Session, user: User) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(id=_token_hash(raw), user_id=user.id, expires_at=utcnow() + timedelta(minutes=settings.password_reset_ttl_minutes)))
    db.commit()
    return raw


def consume_reset_token(db: Session, raw: str) -> User | None:
    row = db.get(PasswordResetToken, _token_hash(raw))
    if not row or row.used_at or row.expires_at < utcnow():
        return None
    row.used_at = utcnow()
    user = db.get(User, row.user_id)
    db.commit()
    return user


# -------------------------------------------------------------------- TOTP ---
# RFC 6238 time based one time passwords (SHA1, 30 second step, 6 digits), so
# any authenticator app works. No dependency: HMAC from the standard library.


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_at(secret: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def verify_totp(secret: str, code: str, *, now: float | None = None, window: int = 1) -> bool:
    if not secret or not code or not code.strip().isdigit():
        return False
    counter = int((now if now is not None else time.time()) // 30)
    return any(hmac.compare_digest(totp_at(secret, counter + d), code.strip()) for d in range(-window, window + 1))


def otpauth_uri(secret: str, account: str, issuer: str = "Lantern") -> str:
    from urllib.parse import quote

    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def new_challenge_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(24)
    return raw, _token_hash(raw)


def challenge_matches(token_hash: str, raw: str | None) -> bool:
    return bool(raw) and hmac.compare_digest(token_hash, _token_hash(raw))
