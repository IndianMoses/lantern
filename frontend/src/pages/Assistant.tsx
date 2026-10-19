import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { MessageBlocks } from "../components/Blocks";
import { Icon } from "../components/Icons";
import { CustomerShell } from "../components/Layout";
import { CitationProvider, Markdown, type ClauseRef } from "../components/Markdown";
import { Badge, Modal, Spinner, useToast } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { useLang } from "../lib/i18n";

export interface Message { id: string; role: string; content: string; blocks: Record<string, unknown>[]; run_id: string | null; language: string; created_at: string }
export interface Run { id: string; intent: string; workflow: string; status: string; provider: string; model: string | null; prompt_version: string; grounding: Record<string, unknown>; plan: Record<string, unknown>[]; tool_executions: { step: number; tool: string; tier: number; status: string; duration_ms: number; error: string | null; input: Record<string, unknown> }[]; input_tokens: number; output_tokens: number }
interface Conversation { id: string; title: string; context: Record<string, unknown>; updated_at: string }

export function clausesFromBlocks(blocks: Record<string, unknown>[]): ClauseRef[] {
  const out: ClauseRef[] = [];
  for (const b of blocks) {
    if (b.type === "citations") out.push(...(b.items as ClauseRef[]));
    if (b.type === "coverage_view") {
      const v = b.view as { covering_clauses: ClauseRef[]; possible_exclusions: ClauseRef[]; conditions: ClauseRef[]; financial_terms: ClauseRef[] };
      out.push(...v.covering_clauses, ...v.possible_exclusions, ...v.conditions, ...v.financial_terms);
    }
    if (b.type === "gap_findings") for (const f of b.findings as { evidence: ClauseRef[] }[]) out.push(...f.evidence);
    if (b.type === "checklist") for (const i of b.items as { citations: ClauseRef[] }[]) out.push(...(i.citations || []));
  }
  return out;
}

export function RunTransparency({ run, lang }: { run: Run; lang: string }) {
  const g = run.grounding as { citations_valid?: number; citations_unverified?: number; caution_banner?: boolean };
  return (
    <div className="transparency stack" style={{ gap: 6 }}>
      <div className="row" style={{ gap: 6 }}>
        <Badge plain>intent: {run.intent}</Badge>
        <Badge plain>{run.provider}{run.model ? ` · ${run.model}` : ""}</Badge>
        <Badge plain>prompt {run.prompt_version}</Badge>
        <Badge tone={g.citations_unverified ? "bad" : "ok"} plain>{lang === "zh" ? "引用核实" : "citations"}: {g.citations_valid ?? 0} {lang === "zh" ? "已核实" : "verified"}{g.citations_unverified ? ` · ${g.citations_unverified} ${lang === "zh" ? "未核实" : "unverified"}` : ""}</Badge>
      </div>
      {run.tool_executions.length > 0 && (
        <table className="table">
          <thead><tr><th>#</th><th>{lang === "zh" ? "工具" : "Tool"}</th><th>{lang === "zh" ? "权限层级" : "Tier"}</th><th>{lang === "zh" ? "结果" : "Outcome"}</th><th>ms</th></tr></thead>
          <tbody>
            {run.tool_executions.map((t) => (
              <tr key={t.step}><td>{t.step}</td><td className="mono">{t.tool}</td><td>{t.tier}</td><td><Badge tone={t.status === "ok" ? "ok" : t.status === "approval_required" ? "accent" : "bad"} plain>{t.status}</Badge>{t.error && <span className="small muted"> {t.error.slice(0, 80)}</span>}</td><td className="muted">{t.duration_ms}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row small muted">
        <span>{lang === "zh" ? "层级 0 读取 · 1 准备 · 2 需你批准 · 3 仅限人工" : "Tier 0 read · 1 prepare · 2 needs your approval · 3 humans only"}</span>
        <a href={`/api/assistant/runs/${run.id}/log`} target="_blank" rel="noreferrer">{lang === "zh" ? "操作日志" : "operation log"} <Icon name="external" size={12} /></a>
      </div>
    </div>
  );
}

export default function AssistantPage() {
  const { t, lang, pick } = useLang();
  const toast = useToast();
  const [params] = useSearchParams();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<Record<string, Run>>({});
  const [text, setText] = useState(params.get("q") || "");
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [showRun, setShowRun] = useState<string | null>(null);
  const [attachOpen, setAttachOpen] = useState(false);
  const [docs, setDocs] = useState<{ id: string; filename: string; doc_type: string; injection_flags: unknown[] }[]>([]);
  const [attachments, setAttachments] = useState<{ id: string; filename: string }[]>([]);
  const bottom = useRef<HTMLDivElement>(null);
  const started = useRef(false);

  const loadList = useCallback(async () => {
    const r = await api.get<{ conversations: Conversation[] }>("/api/assistant/conversations");
    setConversations(r.conversations);
    return r.conversations;
  }, []);

  const openConversation = useCallback(async (id: string) => {
    const r = await api.get<{ conversation: Conversation; messages: Message[] }>(`/api/assistant/conversations/${id}`);
    setCurrent(r.conversation);
    setMessages(r.messages);
    setRuns({});
  }, []);

  const newConversation = useCallback(async (context?: Record<string, string>) => {
    const r = await api.post<{ conversation: Conversation }>("/api/assistant/conversations", { context: context || {} });
    setCurrent(r.conversation);
    setMessages([]);
    setRuns({});
    loadList();
    return r.conversation;
  }, [loadList]);

  useEffect(() => {
    api.get<{ suggestions: string[] }>("/api/assistant/suggestions").then((r) => setSuggestions(r.suggestions)).catch(() => {});
  }, [lang]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      const ctx: Record<string, string> = {};
      if (params.get("claim")) ctx.claim_id = params.get("claim")!;
      if (params.get("policy")) ctx.policy_id = params.get("policy")!;
      const list = await loadList();
      if (Object.keys(ctx).length || !list.length) await newConversation(ctx);
      else await openConversation(list[0].id);
    })();
  }, [loadList, newConversation, openConversation, params]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const send = async (override?: string) => {
    const msg = (override ?? text).trim();
    if (!msg || busy) return;
    let conv = current;
    if (!conv) conv = await newConversation();
    setBusy(true);
    setText("");
    const optimistic: Message = { id: `tmp-${Date.now()}`, role: "user", content: msg, blocks: [], run_id: null, language: lang, created_at: new Date().toISOString() };
    setMessages((m) => [...m, optimistic]);
    try {
      const r = await api.post<{ message: Message; run: Run; conversation: Conversation }>(`/api/assistant/conversations/${conv.id}/messages`, { text: msg, language: lang, attachment_ids: attachments.map((a) => a.id) });
      setMessages((m) => [...m, r.message]);
      setRuns((rs) => ({ ...rs, [r.run.id]: r.run }));
      setCurrent(r.conversation);
      setAttachments([]);
      loadList();
    } catch (e) {
      toast(errorMessage(e));
      setMessages((m) => m.filter((x) => x.id !== optimistic.id));
      setText(msg);
    } finally {
      setBusy(false);
    }
  };

  const openAttach = async () => {
    const r = await api.get<{ documents: typeof docs }>("/api/documents");
    setDocs(r.documents);
    setAttachOpen(true);
  };
  const uploadAttach = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("doc_type", "correspondence");
    try {
      const r = await api.upload<{ document: { id: string; filename: string } }>("/api/documents/upload", form);
      setAttachments((a) => [...a, r.document]);
      setAttachOpen(false);
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const allClauses = clausesFromBlocks(messages.flatMap((m) => m.blocks || []));
  const ctxNote = current?.context?.claim_id ? (lang === "zh" ? "当前对话关联到一个理赔。" : "This conversation is focused on a claim.") : current?.context?.policy_id ? (lang === "zh" ? "当前对话关联到一份保单。" : "This conversation is focused on a policy.") : null;

  return (
    <CustomerShell title={t("assistant")} subtitle={lang === "zh" ? "基于你自己的保单条款作答，所有外发操作需你批准。" : "Answers from your own policy wording, with citations. Anything consequential needs your approval."}>
      <CitationProvider clauses={allClauses}>
        <div className="chat">
          <div className="thread">
            <div className="messages">
              {messages.length === 0 && (
                <div className="empty" style={{ border: 0 }}>
                  <h3 style={{ marginBottom: 6 }}>{lang === "zh" ? "我能帮你做什么？" : "What can I help with?"}</h3>
                  <p className="muted">{lang === "zh" ? "问保单问题、分析保障缺口，或描述发生了什么。" : "Ask about your cover, check for gaps, or tell me what happened."}</p>
                </div>
              )}
              {messages.map((m) => (
                <div key={m.id} className={`msg ${m.role}`}>
                  {m.role === "user" ? (
                    <>
                      <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
                      {m.blocks?.some((b) => b.type === "attachments") && (
                        <div className="small muted"><Icon name="paperclip" size={13} /> {((m.blocks.find((b) => b.type === "attachments") as { documents: { filename: string }[] }).documents || []).map((d) => d.filename).join(", ")}</div>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="bubble">
                        <Markdown text={m.content} />
                        <MessageBlocks blocks={m.blocks || []} />
                      </div>
                      <div className="meta">
                        <span>{fmtDateTime(m.created_at, lang)}</span>
                        {m.run_id && (
                          <button className="btn ghost sm" style={{ padding: 0 }} onClick={async () => { if (!runs[m.run_id!]) { const r = await api.get<{ run: Run }>(`/api/assistant/runs/${m.run_id}`); setRuns((rs) => ({ ...rs, [m.run_id!]: r.run })); } setShowRun(showRun === m.run_id ? null : m.run_id); }}>
                            {t("why_this_answer")} <Icon name={showRun === m.run_id ? "chevronUp" : "chevronDown"} size={13} />
                          </button>
                        )}
                      </div>
                      {showRun === m.run_id && runs[m.run_id!] && <div className="block"><RunTransparency run={runs[m.run_id!]} lang={lang} /></div>}
                    </>
                  )}
                </div>
              ))}
              {busy && <div className="msg assistant"><div className="bubble row"><Spinner /><span className="muted">{lang === "zh" ? "正在检索条款并执行工具…" : "Reading the wording and running tools…"}</span></div></div>}
              <div ref={bottom} />
            </div>
            <div className="composer">
              {messages.length < 2 && suggestions.length > 0 && (
                <div className="suggestions">{suggestions.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}</div>
              )}
              {attachments.length > 0 && <div className="row small"><Icon name="paperclip" size={13} /> {attachments.map((a) => <Badge key={a.id} plain>{a.filename} <button className="btn ghost sm" style={{ padding: 0, minHeight: 0 }} aria-label={t("close")} onClick={() => setAttachments((x) => x.filter((y) => y.id !== a.id))}><Icon name="x" size={12} /></button></Badge>)}</div>}
              <textarea className="textarea" placeholder={t("ask_placeholder")} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
              <div className="row between">
                <div className="row"><button className="btn sm" onClick={openAttach}><Icon name="paperclip" size={14} /> {t("attach")}</button>{ctxNote && <span className="small muted">{ctxNote}</span>}</div>
                <button className="btn primary" disabled={busy || !text.trim()} onClick={() => send()}>{t("send")}</button>
              </div>
            </div>
          </div>
          <aside className="side">
            <div className="card flat">
              <div className="row between"><h3 style={{ margin: 0 }}>{lang === "zh" ? "对话" : "Conversations"}</h3><button className="btn sm" onClick={() => newConversation()}>+ {t("new_conversation")}</button></div>
              <div className="stack" style={{ gap: 4, marginTop: 10 }}>
                {conversations.map((c) => (
                  <button key={c.id} className="btn" style={{ justifyContent: "flex-start", textAlign: "left", whiteSpace: "normal", borderColor: current?.id === c.id ? "var(--accent)" : undefined }} onClick={() => openConversation(c.id)}>
                    <span style={{ fontSize: 13 }}>{c.title}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="card flat small">
              <h3>{lang === "zh" ? "助手如何工作" : "How the assistant works"}</h3>
              <ul style={{ paddingLeft: 16, margin: 0, color: "var(--ink-2)" }}>
                <li>{lang === "zh" ? "每个保障结论都引用具体条款，可点击查看原文。" : "Every statement about cover cites a clause you can open."}</li>
                <li>{lang === "zh" ? "条款没有规定时，它会明确告诉你，而不是猜。" : "When the wording is silent it says so instead of guessing."}</li>
                <li>{lang === "zh" ? "提交理赔或发送信件都需要你批准。" : "Submitting a claim or sending a message always needs your approval."}</li>
                <li>{lang === "zh" ? "是否赔付由保险公司决定。" : "Whether a claim is paid is the insurer's decision."}</li>
              </ul>
            </div>
          </aside>
        </div>
      </CitationProvider>
      {attachOpen && (
        <Modal title={t("attach")} onClose={() => setAttachOpen(false)}>
          <div className="stack">
            <div className="row">
              <label className="btn primary">
                <Icon name="upload" size={16} />{t("upload")}
                <input type="file" style={{ display: "none" }} accept=".pdf,.txt,.md,.png,.jpg,.jpeg,.webp" onChange={(e) => e.target.files?.[0] && uploadAttach(e.target.files[0])} />
              </label>
              <label className="btn">
                <Icon name="camera" size={16} />{t("take_photo")}
                <input type="file" style={{ display: "none" }} accept="image/*" capture="environment" onChange={(e) => e.target.files?.[0] && uploadAttach(e.target.files[0])} />
              </label>
            </div>
            <div className="small muted">{lang === "zh" ? "或选择已上传的文件：" : "Or pick an existing document:"}</div>
            {docs.map((d) => (
              <button key={d.id} className="btn" style={{ justifyContent: "space-between" }} onClick={() => { setAttachments((a) => [...a, { id: d.id, filename: d.filename }]); setAttachOpen(false); }}>
                <span>{d.filename}</span><span className="small muted">{d.doc_type}{d.injection_flags?.length ? <Icon name="warning" size={13} /> : null}</span>
              </button>
            ))}
          </div>
        </Modal>
      )}
      <span className="sr-only">{pick({ en: "", zh: "" })}</span>
    </CustomerShell>
  );
}
