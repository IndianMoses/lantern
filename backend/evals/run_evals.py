"""Run the assistant evaluation suite.

    python -m evals.run_evals                 # offline, deterministic provider
    python -m evals.run_evals --provider anthropic   # against the Claude API (needs ANTHROPIC_API_KEY)

Writes evals/results-<provider>.json and prints a summary table. Each case
runs in a fresh, seeded, isolated database so results are reproducible.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="deterministic", choices=["deterministic", "anthropic"])
    ap.add_argument("--only", default=None, help="comma separated case ids")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="lantern-evals-"))
    os.environ.update({
        "APP_ENV": "test",
        "DATABASE_URL": f"sqlite:///{tmp / 'evals.db'}",
        "UPLOAD_DIR": str(tmp / "uploads"),
        "OPERATION_LOG_DIR": str(tmp / "logs"),
        "LLM_PROVIDER": args.provider,
        "SECRET_KEY": "evals-secret",
    })
    if args.provider == "anthropic" and not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient

    from app.db import SessionLocal, init_db
    from app.main import app
    from app.seed.seed import seed
    from app.security import limiter

    init_db()
    db = SessionLocal()
    seed(db, demo_dir=tmp / "uploads" / "demo")
    db.close()

    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    creds = {"alex": ("alex.tan@demo.example", "alex-demo-2026"), "liwen": ("li.wen@demo.example", "liwen-demo-2026"), "priya": ("priya.nair@demo.example", "priya-demo-2026")}
    results = []
    with TestClient(app) as client:
        sessions: dict[str, tuple[TestClient, str]] = {}
        for case in cases:
            limiter.reset()
            user = case["user"]
            if user not in sessions:
                c = TestClient(app)
                r = c.post("/api/auth/login", json={"email": creds[user][0], "password": creds[user][1]})
                sessions[user] = (c, r.json()["csrf_token"])
            c, csrf = sessions[user]
            h = {"X-CSRF-Token": csrf}
            context = {}
            if case.get("context", {}).get("claim_ref"):
                claims = c.get("/api/claims").json()["claims"]
                match = next((x for x in claims if x["reference"] == case["context"]["claim_ref"]), None)
                if match:
                    context["claim_id"] = match["id"]
            attachment_ids = []
            if case.get("upload"):
                up = case["upload"]
                r = c.post("/api/documents/upload", files={"file": (up["filename"], up["content"].encode("utf-8"), "text/plain")}, data={"doc_type": "correspondence"}, headers=h)
                attachment_ids = [r.json()["document"]["id"]]
            before = len(c.get("/api/approvals").json()["approvals"])
            conv = c.post("/api/assistant/conversations", json={"context": context}, headers=h).json()["conversation"]["id"]
            r = c.post(f"/api/assistant/conversations/{conv}/messages", json={"text": case["text"], "attachment_ids": attachment_ids}, headers=h)
            ok_http = r.status_code == 200
            data = r.json() if ok_http else {}
            after = len(c.get("/api/approvals").json()["approvals"])
            msg = data.get("message", {})
            run = data.get("run", {})
            text = msg.get("content", "")
            blocks = msg.get("blocks", [])
            log = c.get(f"/api/assistant/runs/{run.get('id')}/log").text if run.get("id") else ""
            cites = {i["citation"] for b in blocks if b.get("type") == "citations" for i in b.get("items", [])}
            grounding = run.get("grounding", {})
            checks: dict[str, bool] = {"http_ok": ok_http}
            e = case["expect"]
            if "intent" in e:
                checks["intent"] = run.get("intent") == e["intent"]
            if "language" in e:
                checks["language"] = msg.get("language") == e["language"]
            if "citations_include" in e:
                checks["citations_include"] = all(x in cites or f"[{x}]" in text for x in e["citations_include"])
            if "citations_include_any" in e:
                checks["citations_include_any"] = any(x in cites or f"[{x}]" in text for x in e["citations_include_any"])
            if "citations_count" in e:
                checks["citations_count"] = len(cites) == e["citations_count"]
            if e.get("no_unverified"):
                checks["no_unverified"] = grounding.get("citations_unverified", 0) == 0
            if e.get("silent"):
                checks["silent"] = grounding.get("citations_valid", 0) == 0
            if e.get("no_approval"):
                checks["no_approval"] = after == before and not any(b.get("type") == "approval" for b in blocks)
            if "max_tier" in e:
                checks["max_tier"] = all(t["tier"] <= e["max_tier"] for t in run.get("tool_executions", []))
            if "tools" in e:
                used = [t["tool"] for t in run.get("tool_executions", [])]
                checks["tools"] = all(t in used for t in e["tools"])
            if "block_types" in e:
                types = {b.get("type") for b in blocks}
                checks["block_types"] = all(t in types for t in e["block_types"])
            low = text.lower()
            if "text_includes_any" in e:
                checks["text_includes_any"] = any(x.lower() in low for x in e["text_includes_any"])
            if "text_includes_all" in e:
                checks["text_includes_all"] = all(x.lower() in low for x in e["text_includes_all"])
            if "text_excludes" in e:
                checks["text_excludes"] = not any(x.lower() in low for x in e["text_excludes"])
            if "log_excludes" in e:
                checks["log_excludes"] = not any(x in log for x in e["log_excludes"]) and not any(x in json.dumps(run) for x in e["log_excludes"])
            passed = all(checks.values())
            results.append({"id": case["id"], "category": e.get("category"), "passed": passed, "checks": checks, "intent": run.get("intent"), "tools": [t["tool"] for t in run.get("tool_executions", [])], "grounding": grounding, "answer_excerpt": text[:400]})
            print(f"{'PASS' if passed else 'FAIL':4} {case['id']:28} {e.get('category', ''):30} " + ", ".join(k for k, v in checks.items() if not v))
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    by_cat: dict[str, list[bool]] = {}
    for r in results:
        by_cat.setdefault(r["category"] or "other", []).append(r["passed"])
    summary = {"provider": args.provider, "ran_at": datetime.now(timezone.utc).isoformat(), "total": total, "passed": passed, "pass_rate": round(passed / total, 3) if total else None, "by_category": {k: {"passed": sum(v), "total": len(v)} for k, v in by_cat.items()}, "results": results}
    out = HERE / f"results-{args.provider}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{passed}/{total} passed ({summary['pass_rate']}) -> {out}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
