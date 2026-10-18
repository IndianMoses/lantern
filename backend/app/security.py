"""Authentication primitives: password hashing, server side sessions,
CSRF double submit tokens, one time approval tokens and rate limiting."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy.orm import Session

from .config import settings
from .models import User, UserSession, utcnow

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


def create_session(db: Session, user: User, ip: str | None = None) -> tuple[str, str]:
    """Returns (raw_session_token, csrf_token). Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(24)
    sess = UserSession(
        id=_token_hash(raw),
        user_id=user.id,
        csrf_token=csrf,
        expires_at=utcnow() + timedelta(hours=settings.session_ttl_hours),
        ip=ip,
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
