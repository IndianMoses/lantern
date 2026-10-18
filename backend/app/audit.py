"""Append only, hash chained audit log.

Each event stores the hash of the previous event and its own hash over a
canonical serialisation, so any edit or deletion in the middle of the chain
is detectable by ``verify_chain``. The chain is global (one sequence)
which keeps verification simple for a single deployment.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import AuditEvent, utcnow
from .pii import mask_value

GENESIS = "0" * 64
_lock = threading.Lock()


def _canonical(seq: int, actor_type: str, actor_id: str | None, action: str, target_type: str | None, target_id: str | None, outcome: str, details: dict, created_at: str, prev_hash: str) -> str:
    return json.dumps(
        {
            "seq": seq,
            "actor_type": actor_type,
            "actor_id": actor_id,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "outcome": outcome,
            "details": details,
            "created_at": created_at,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )


def record(
    db: Session,
    *,
    actor_type: str,
    actor_id: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> AuditEvent:
    safe_details = mask_value(details or {})
    with _lock:
        last = db.execute(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1)).scalar_one_or_none()
        seq = (last.seq + 1) if last else 1
        prev_hash = last.hash if last else GENESIS
        created_at = utcnow()
        digest = hashlib.sha256(
            _canonical(seq, actor_type, actor_id, action, target_type, target_id, outcome, safe_details, created_at.isoformat(), prev_hash).encode("utf-8")
        ).hexdigest()
        ev = AuditEvent(
            seq=seq,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            details=safe_details,
            prev_hash=prev_hash,
            hash=digest,
            created_at=created_at,
        )
        db.add(ev)
        if commit:
            db.commit()
        else:
            db.flush()
    return ev


def verify_chain(db: Session) -> dict[str, Any]:
    events = db.execute(select(AuditEvent).order_by(AuditEvent.seq.asc())).scalars().all()
    prev = GENESIS
    expected_seq = 1
    for ev in events:
        if ev.seq != expected_seq:
            return {"ok": False, "checked": expected_seq - 1, "error": f"sequence gap at {ev.seq}"}
        if ev.prev_hash != prev:
            return {"ok": False, "checked": expected_seq - 1, "error": f"broken link at seq {ev.seq}"}
        recomputed = hashlib.sha256(
            _canonical(ev.seq, ev.actor_type, ev.actor_id, ev.action, ev.target_type, ev.target_id, ev.outcome, ev.details, ev.created_at.isoformat(), ev.prev_hash).encode("utf-8")
        ).hexdigest()
        if recomputed != ev.hash:
            return {"ok": False, "checked": expected_seq - 1, "error": f"hash mismatch at seq {ev.seq}"}
        prev = ev.hash
        expected_seq += 1
    total = db.execute(select(func.count(AuditEvent.id))).scalar_one()
    return {"ok": True, "checked": total, "head": prev}
