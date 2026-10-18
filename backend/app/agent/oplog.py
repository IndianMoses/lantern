"""Operation logs: one JSON Lines file per agent run, plus an index.

These are the sandbox execution evidence for the competition and the
material an auditor would read. Inputs are masked before writing; document
text is never written, only identifiers, counts and citations.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import settings
from ..pii import mask_value

_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperationLog:
    def __init__(self, run_id: str, *, user_id: str, surface: str, provider: str, model: str | None, prompt_version: str, intent: str) -> None:
        self.run_id = run_id
        self.dir = Path(settings.operation_log_dir) / datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{run_id}.jsonl"
        self.index = Path(settings.operation_log_dir) / "index.jsonl"
        self.events: list[dict] = []
        self.write("run_started", user_id=user_id, surface=surface, provider=provider, model=model, prompt_version=prompt_version, intent=intent, environment=settings.app_env)

    def write(self, event: str, **data: Any) -> None:
        rec = {"ts": _now(), "run_id": self.run_id, "event": event, **mask_value(data)}
        self.events.append(rec)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with _lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def tool(self, **data: Any) -> None:
        self.write("tool_call", **data)

    def finish(self, *, status: str, tokens: dict, grounding: dict, approvals: int, error: str | None = None) -> None:
        self.write("run_finished", status=status, tokens=tokens, grounding=grounding, approvals_created=approvals, error=error)
        summary = {"ts": _now(), "run_id": self.run_id, "status": status, "events": len(self.events), "file": str(self.path.relative_to(Path(settings.operation_log_dir).parent.parent)) if str(self.path).startswith(str(Path(settings.operation_log_dir).parent.parent)) else str(self.path), "tool_calls": sum(1 for e in self.events if e["event"] == "tool_call"), "approvals_created": approvals}
        with _lock:
            with self.index.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
