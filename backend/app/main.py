"""Lantern API application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from . import audit
from .config import REPO_ROOT, settings
from .db import SessionLocal, init_db
from .models import User
from .routers import approvals, assistant, auth, claims, communications, dev, documents, gaps, insurer, policies, profile

log = logging.getLogger("lantern")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.operation_log_dir).mkdir(parents=True, exist_ok=True)
    if settings.is_dev:
        db = SessionLocal()
        try:
            if not db.execute(select(User.id).limit(1)).first():
                from .seed.seed import seed

                result = seed(db)
                log.info("Seeded demo environment: %s", result)
        finally:
            db.close()
    log.info("Lantern started (env=%s, llm=%s, model=%s)", settings.app_env, settings.effective_llm_provider, settings.llm_model)
    yield


app = FastAPI(title="Lantern — insurance assistant API", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")

if settings.is_dev:
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal error. The action was not completed."})


for r in (auth.router, profile.router, policies.router, documents.router, gaps.router, claims.router, communications.router, approvals.router, assistant.router, insurer.router):
    app.include_router(r)
if settings.is_dev:
    app.include_router(dev.router)


@app.get("/api/health")
def health():
    db = SessionLocal()
    try:
        chain = audit.verify_chain(db)
    finally:
        db.close()
    return {"status": "ok", "env": settings.app_env, "llm_provider": settings.effective_llm_provider, "model": settings.llm_model if settings.effective_llm_provider == "anthropic" else None, "audit_chain_ok": chain["ok"], "audit_events": chain.get("checked")}


# ---- frontend (built SPA) ----
DIST = REPO_ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        candidate = DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
