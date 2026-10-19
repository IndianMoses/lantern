"""Test fixtures: isolated database, upload and log directories, offline provider."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="lantern-tests-"))
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["OPERATION_LOG_DIR"] = str(_TMP / "logs")
os.environ["LLM_PROVIDER"] = "deterministic"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["ANALYSIS_RUN_IN_BACKGROUND"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed.seed import seed  # noqa: E402
from app.security import limiter  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _seeded():
    init_db()
    db = SessionLocal()
    try:
        seed(db, demo_dir=_TMP / "uploads" / "demo")
    finally:
        db.close()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client():
    limiter.reset()
    with TestClient(app) as c:
        yield c


class Session:
    def __init__(self, client: TestClient, email: str, password: str):
        self.client = TestClient(app)
        r = self.client.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf_token"]
        self.user = r.json()["user"]

    @property
    def h(self) -> dict:
        return {"X-CSRF-Token": self.csrf}

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, **kw):
        kw.setdefault("headers", {}).update(self.h)
        return self.client.post(url, **kw)

    def patch(self, url, **kw):
        kw.setdefault("headers", {}).update(self.h)
        return self.client.patch(url, **kw)

    def put(self, url, **kw):
        kw.setdefault("headers", {}).update(self.h)
        return self.client.put(url, **kw)

    def delete(self, url, **kw):
        kw.setdefault("headers", {}).update(self.h)
        return self.client.delete(url, **kw)

    def chat(self, text: str, conversation_id: str | None = None, context: dict | None = None, attachment_ids: list[str] | None = None) -> dict:
        if not conversation_id:
            r = self.post("/api/assistant/conversations", json={"context": context or {}})
            assert r.status_code == 200, r.text
            conversation_id = r.json()["conversation"]["id"]
        r = self.post(f"/api/assistant/conversations/{conversation_id}/messages", json={"text": text, "attachment_ids": attachment_ids or []})
        assert r.status_code == 200, r.text
        data = r.json()
        data["conversation_id"] = conversation_id
        return data


@pytest.fixture
def login(client):
    limiter.reset()

    def _login(email: str, password: str) -> Session:
        return Session(client, email, password)

    return _login


@pytest.fixture
def alex(login):
    return login("alex.tan@demo.example", "alex-demo-2026")


@pytest.fixture
def liwen(login):
    return login("li.wen@demo.example", "liwen-demo-2026")


@pytest.fixture
def priya(login):
    return login("priya.nair@demo.example", "priya-demo-2026")


@pytest.fixture
def jordan(login):
    return login("jordan.reyes@northgate.example", "jordan-demo-2026")


@pytest.fixture
def wanglei(login):
    return login("wang.lei@pengcheng.example", "wanglei-demo-2026")


@pytest.fixture
def morgan(login):
    return login("morgan.clarke@northgate.example", "morgan-demo-2026")


@pytest.fixture
def tmp_root():
    return _TMP
