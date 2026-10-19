from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent.runner import run_turn
from ..config import settings
from ..db import get_db
from ..deps import Principal, get_principal, require_customer, require_insurer
from ..models import AgentRun, Conversation, Document, Message, ToolExecution
from ..security import limiter
from ..agent.tools import REGISTRY

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class ConversationIn(BaseModel):
    context: dict = Field(default_factory=dict)
    surface: str = Field("customer", pattern="^(customer|insurer_copilot)$")


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)
    language: str | None = Field(None, pattern="^(en|zh)$")
    context: dict | None = None


def message_out(m: Message) -> dict:
    return {"id": m.id, "role": m.role, "content": m.content, "blocks": m.blocks, "run_id": m.run_id, "language": m.language, "created_at": m.created_at.isoformat()}


def conversation_out(c: Conversation) -> dict:
    return {"id": c.id, "title": c.title, "context": c.context, "surface": c.surface, "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat()}


def _conversation(db: Session, p: Principal, conversation_id: str) -> Conversation:
    c = db.get(Conversation, conversation_id)
    if not c or c.user_id != p.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return c


@router.get("/conversations")
def list_conversations(surface: str = "customer", p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    rows = db.execute(select(Conversation).where(Conversation.user_id == p.id, Conversation.surface == surface).order_by(Conversation.updated_at.desc()).limit(30)).scalars().all()
    return {"conversations": [conversation_out(c) for c in rows]}


@router.post("/conversations")
def create_conversation(body: ConversationIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    if body.surface == "insurer_copilot" and not p.user.is_insurer:
        raise HTTPException(status_code=403, detail="Insurer staff only")
    if body.surface == "customer" and p.user.is_insurer:
        raise HTTPException(status_code=403, detail="Customer surface only")
    ctx = {k: v for k, v in body.context.items() if k in {"claim_id", "policy_id"} and isinstance(v, str)}
    c = Conversation(user_id=p.id, context=ctx, surface=body.surface)
    db.add(c)
    db.commit()
    return {"conversation": conversation_out(c)}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    c = _conversation(db, p, conversation_id)
    msgs = db.execute(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at.asc(), Message.id)).scalars().all()
    return {"conversation": conversation_out(c), "messages": [message_out(m) for m in msgs]}


@router.post("/conversations/{conversation_id}/messages")
def post_message(conversation_id: str, body: MessageIn, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    c = _conversation(db, p, conversation_id)
    if not limiter.allow(f"assistant:{p.id}", settings.rate_limit_assistant_per_minute):
        raise HTTPException(status_code=429, detail="Too many requests to the assistant; please wait a moment")
    attachments: list[Document] = []
    if body.attachment_ids:
        if p.user.is_insurer:
            raise HTTPException(status_code=400, detail="Attachments are not supported in the copilot")
        attachments = list(db.execute(select(Document).where(Document.id.in_(body.attachment_ids), Document.owner_user_id == p.id)).scalars())
        if len(attachments) != len(set(body.attachment_ids)):
            raise HTTPException(status_code=404, detail="Attachment not found")
    if body.context:
        merged = dict(c.context or {})
        for k in ("claim_id", "policy_id"):
            if isinstance(body.context.get(k), str):
                merged[k] = body.context[k]
        c.context = merged
    result = run_turn(db, p.user, c, body.text, language=body.language, attachments=attachments, surface=c.surface)
    return {"message": message_out(result.message), "run": run_out(db, result.run), "approvals": result.approvals, "conversation": conversation_out(c)}


def run_out(db: Session, run: AgentRun) -> dict:
    execs = db.execute(select(ToolExecution).where(ToolExecution.run_id == run.id).order_by(ToolExecution.step)).scalars().all()
    return {
        "id": run.id, "intent": run.intent, "workflow": run.workflow, "status": run.status, "provider": run.provider, "model": run.model, "prompt_version": run.prompt_version,
        "input_tokens": run.input_tokens, "output_tokens": run.output_tokens, "grounding": run.grounding, "error": run.error, "started_at": run.started_at.isoformat(), "ended_at": run.ended_at.isoformat() if run.ended_at else None,
        "plan": run.plan,
        "tool_executions": [{"step": e.step, "tool": e.tool_name, "tier": e.tier, "status": e.status, "input": e.input_masked, "output_summary": e.output_summary, "duration_ms": e.duration_ms, "error": e.error, "approval_id": e.approval_id} for e in execs],
    }


@router.get("/runs/{run_id}")
def get_run(run_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    run = db.get(AgentRun, run_id)
    if not run or run.user_id != p.id:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": run_out(db, run)}


@router.get("/runs/{run_id}/log", response_class=PlainTextResponse)
def get_run_log(run_id: str, p: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    run = db.get(AgentRun, run_id)
    if not run or run.user_id != p.id:
        raise HTTPException(status_code=404, detail="Run not found")
    base = Path(settings.operation_log_dir)
    for path in base.glob(f"*/{run_id}.jsonl"):
        return path.read_text(encoding="utf-8")
    raise HTTPException(status_code=404, detail="Log not found")


@router.get("/tools")
def list_tools(p: Principal = Depends(get_principal)):
    surface = "insurer_copilot" if p.user.is_insurer else "customer"
    return {"tools": [{"name": s.name, "tier": s.tier, "description": s.description, "surface": sorted(s.surfaces)} for s in REGISTRY.values() if p.role in s.roles and surface in s.surfaces]}


@router.get("/suggestions")
def suggestions(p: Principal = Depends(get_principal)):
    lang = p.lang
    if p.user.is_insurer:
        return {"suggestions": ["Summarise this claim", "What is missing from the pack?", "Draft a request for the missing documents", "Which clauses bear on this incident?"] if lang != "zh" else ["总结这个案件", "材料包还缺什么？", "起草补充材料请求", "哪些条款与本次事故相关？"]}
    if lang == "zh":
        return {"suggestions": ["总结我的所有保单", "充电起火算不算？", "分析我的保障缺口", "我的电动车昨晚在楼下被偷了，报了警", "我的理赔进度怎么样了", "帮我写一封催进度的邮件"]}
    return {"suggestions": ["Summarise all my policies", "Does my home policy cover accidental damage?", "Am I covered in the USA?", "Find gaps and overlaps in my cover", "My road bike was stolen from outside the office yesterday. It was locked and I reported it to the police.", "What's the status of my claim?", "Draft an email asking my insurer for an update"]}
