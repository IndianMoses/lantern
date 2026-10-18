"""Development only helpers. Mounted only when APP_ENV != production."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import get_db
from ..seed.seed import demo_accounts, seed

router = APIRouter(prefix="/api/dev", tags=["dev"])


@router.get("/demo-accounts")
def accounts():
    if not settings.is_dev or not settings.expose_demo_accounts:
        raise HTTPException(status_code=404, detail="Not found")
    return {"note": "Development only. These accounts exist only in the seeded demo database.", "accounts": demo_accounts()}


@router.post("/reset-demo")
def reset(db: Session = Depends(get_db)):
    if not settings.is_dev:
        raise HTTPException(status_code=404, detail="Not found")
    result = seed(db)
    audit.record(db, actor_type="system", actor_id=None, action="seed.reset", details=result)
    return {"ok": True, **result}


@router.get("/audit-chain")
def chain(db: Session = Depends(get_db)):
    if not settings.is_dev:
        raise HTTPException(status_code=404, detail="Not found")
    return audit.verify_chain(db)
