import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Checklist, ChecksPanel, CompletenessPanel, CoverageViewPanel, MessageBlocks, type ChecklistItem, type Checks, type Completeness, type CoverageView } from "../components/Blocks";
import { Icon } from "../components/Icons";
import { InsurerShell } from "../components/Layout";
import { CitationChip, CitationProvider, Markdown, type ClauseRef } from "../components/Markdown";
import { Badge, Field, Spinner, StatusBadge, useToast } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { fmtDate, fmtDateTime, money } from "../lib/format";
import { useLang } from "../lib/i18n";
import { RunTransparency, clausesFromBlocks, type Message, type Run } from "../pages/Assistant";
import type { PolicyCard } from "../pages/Overview";
import type { QueueRow } from "./Queue";

interface Workspace {
  claim: QueueRow & { description: string; incident_time: string | null; location: string | null; items_involved: { label: string; value?: number }[]; loss_unknown: boolean; third_party_involved: boolean | null; authorities_involved: boolean | null; authority_reference: string | null; actions_taken: string | null; safety_concern: boolean; language: string; acknowledged_at: string | null; first_response_at: string | null; status_source: string; assigned_handler_id: string | null };
  customer: { id: string; name: string; email: string; locale: string };
  policy: PolicyCard | null;
  clauses: (ClauseRef & { id: string })[];
  documents: { id: string; filename: string; doc_type: string; checklist_item_key: string | null; extraction_status: string; injection_flags: { type: string; excerpt: string }[]; sha256: string; created_at: string; extracted_excerpt: string }[];
  checklist: ChecklistItem[];
  completeness: Completeness;
  coverage_reading: CoverageView & { label: string };
  information_requests: { id: string; message: string; requested_items: string[]; due_date: string | null; status: string; response_text: string | null; responded_at: string | null; requested_by: string | null; created_at: string }[];
  communications: { id: string; direction: string; kind: string; subject: string; body: string; sent_at: string | null; created_at: string }[];
  decisions: { id: string; outcome: string; reason: string; clause_refs: string[]; approved_amount: number | null; currency: string; decided_by: string | null; created_at: string }[];
  submissions: { id: string; status: string; adapter: string; mode: string; insurer_reference: string | null; mock: boolean; error: string | null; created_at: string }[];
  internal_notes: { id: string; author: string; text: string; created_at: string }[];
  timeline: { id: string; at: string; actor_type: string; event_type: string; summary: string }[];
  audit: { seq: number; action: string; actor_type: string; outcome: string; created_at: string; hash: string }[];
  allowed_statuses: string[];
  can_decide: boolean;
  checks: Checks | null;
}

export default function WorkspacePage() {
  const { id } = useParams();
  const { t, lang, pick } = useLang();
  const toast = useToast();
  const [w, setW] = useState<Workspace | null>(null);
  const [team, setTeam] = useState<{ id: string; name: string }[]>([]);
  const [tab, setTab] = useState<"case" | "documents" | "messages" | "audit">("case");
  const [req, setReq] = useState({ message: "", items: "", due_date: "" });
  const [dec, setDec] = useState({ outcome: "approved", reason: "", clause_refs: "", approved_amount: "" });
  const [note, setNote] = useState("");
  const [copilotConv, setCopilotConv] = useState<string | null>(null);
  const [copilotMsgs, setCopilotMsgs] = useState<Message[]>([]);
  const [copilotRuns, setCopilotRuns] = useState<Record<string, Run>>({});
  const [copilotText, setCopilotText] = useState("");
  const [copilotBusy, setCopilotBusy] = useState(false);
  const [showRun, setShowRun] = useState<string | null>(null);
  const load = useCallback(() => api.get<Workspace>(`/api/insurer/claims/${id}`).then(setW), [id, lang]);
  useEffect(() => { load(); api.get<{ team: typeof team }>("/api/insurer/team").then((r) => setTeam(r.team)); setCopilotConv(null); setCopilotMsgs([]); }, [id, load]);
  if (!w) return <InsurerShell><Spinner /></InsurerShell>;
  const c = w.claim;
  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    try {
      await fn();
      if (ok) toast(ok);
      load();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const copilot = async (text: string) => {
    if (!text.trim()) return;
    setCopilotBusy(true);
    try {
      let conv = copilotConv;
      if (!conv) {
        const r = await api.post<{ conversation: { id: string } }>("/api/assistant/conversations", { surface: "insurer_copilot", context: { claim_id: c.id } });
        conv = r.conversation.id;
        setCopilotConv(conv);
      }
      setCopilotMsgs((m) => [...m, { id: `u${Date.now()}`, role: "user", content: text, blocks: [], run_id: null, language: lang, created_at: new Date().toISOString() }]);
      const r = await api.post<{ message: Message; run: Run }>(`/api/assistant/conversations/${conv}/messages`, { text, language: lang });
      setCopilotMsgs((m) => [...m, r.message]);
      setCopilotRuns((rs) => ({ ...rs, [r.run.id]: r.run }));
      const draft = r.message.blocks.find((b) => b.type === "insurer_request_draft") as { message: string; requested_items: string[] } | undefined;
      if (draft) setReq({ message: draft.message, items: draft.requested_items.join("\n"), due_date: "" });
      setCopilotText("");
    } catch (e) {
      toast(errorMessage(e));
    } finally {
      setCopilotBusy(false);
    }
  };
  const clauses: ClauseRef[] = [...w.clauses, ...clausesFromBlocks(copilotMsgs.flatMap((m) => m.blocks || []))];
  return (
    <InsurerShell title={c.reference} subtitle={`${c.insurance_type_label} · ${c.incident_type_label} · ${w.customer.name}`} actions={<Link className="btn sm" to="/insurer/claims"><Icon name="arrowLeft" size={14} /> {t("queue")}</Link>}>
      <CitationProvider clauses={clauses}>
        <div className="card" style={{ marginBottom: 14, display: "flex", gap: 24, flexWrap: "wrap", alignItems: "center" }}>
          <div><div className="small muted">{t("status")}</div><StatusBadge status={c.status} label={c.status_label} /> <span className="small muted">{c.status_source} · {fmtDateTime(c.updated_at, lang)}</span></div>
          <div><div className="small muted">{lang === "zh" ? "提交" : "Submitted"}</div>{fmtDateTime(c.submitted_at, lang)} {c.age_days != null && <span className="small muted">({c.age_days}d{c.sla_breach ? " · SLA" : ""})</span>}</div>
          <div><div className="small muted">{lang === "zh" ? "首次响应" : "First response"}</div>{c.first_response_at ? fmtDateTime(c.first_response_at, lang) : <span className="muted">—</span>}</div>
          <div><div className="small muted">{t("handler")}</div>{c.assigned_handler || <span className="muted">{t("unassigned")}</span>}</div>
          <div><div className="small muted">{lang === "zh" ? "优先级" : "Priority"}</div><Badge tone={c.priority === "high" ? "bad" : ""} plain>{c.priority}</Badge></div>
          <div><div className="small muted">{lang === "zh" ? "客户预计损失" : "Customer estimate"}</div>{c.loss_unknown ? "unknown" : money(c.estimated_loss, c.currency, lang)}</div>
          <div><div className="small muted">{lang === "zh" ? "受理编号" : "Reference"}</div><span className="mono">{c.insurer_reference || "—"}</span></div>
        </div>
        <div className="workspace" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(320px, 360px)" }}>
          <div>
            <div className="tabs">{(["case", "documents", "messages", "audit"] as const).map((k) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{{ case: lang === "zh" ? "案件" : "Case", documents: `${lang === "zh" ? "文件" : "Documents"} (${w.documents.length})`, messages: `${lang === "zh" ? "沟通" : "Communications"} (${w.communications.length})`, audit: lang === "zh" ? "时间线与审计" : "Timeline & audit" }[k]}</button>)}</div>
            {tab === "case" && (
              <div className="stack">
                <div className="card">
                  <div className="card-head"><h3>{lang === "zh" ? "客户陈述" : "Customer statement"}</h3><Badge plain>source: customer</Badge></div>
                  <dl className="kv">
                    <dt>{lang === "zh" ? "日期" : "Date"}</dt><dd>{fmtDate(c.incident_date, lang)} {c.incident_time || ""}</dd>
                    <dt>{lang === "zh" ? "地点" : "Location"}</dt><dd>{c.location || "—"}</dd>
                    <dt>{lang === "zh" ? "经过" : "Description"}</dt><dd style={{ whiteSpace: "pre-wrap" }}>{c.description}</dd>
                    <dt>{lang === "zh" ? "物品" : "Items"}</dt><dd>{c.items_involved.map((i, k) => <div key={k}>{i.label}{i.value != null ? ` · ${money(i.value, c.currency, lang)}` : ""}</div>)}</dd>
                    <dt>{lang === "zh" ? "报案" : "Authorities"}</dt><dd>{c.authorities_involved ? "yes" : c.authorities_involved === false ? "no" : "—"} {c.authority_reference && <span className="mono">{c.authority_reference}</span>}</dd>
                    <dt>{lang === "zh" ? "第三方" : "Third party"}</dt><dd>{c.third_party_involved ? "yes" : c.third_party_involved === false ? "no" : "—"}</dd>
                    <dt>{lang === "zh" ? "已采取措施" : "Actions taken"}</dt><dd style={{ whiteSpace: "pre-wrap" }}>{c.actions_taken || "—"}</dd>
                  </dl>
                </div>
                {w.policy && (
                  <div className="card">
                    <div className="card-head"><h3>{w.policy.provider_name} {w.policy.product_name}</h3><Badge plain>{w.policy.policy_number}</Badge></div>
                    <div className="small muted">{t("period")}: {fmtDate(w.policy.start_date, lang)} – {fmtDate(w.policy.end_date, lang)} · {t("excess")}: {money(w.policy.excess_amount, w.policy.currency, lang)} · {w.clauses.length} {lang === "zh" ? "条条款" : "clauses"}</div>
                    {c.incident_date && w.policy && (c.incident_date < w.policy.start_date || c.incident_date > w.policy.end_date) && <div className="alert bad" style={{ marginTop: 8 }}>{lang === "zh" ? "事件日期不在保险期间内。" : "Incident date falls outside the policy period."}</div>}
                  </div>
                )}
                <div className="card"><div className="row between" style={{ marginBottom: 6 }}><Badge plain>source: platform rules</Badge><span className="small muted">{w.coverage_reading.label}</span></div><CoverageViewPanel v={w.coverage_reading} /></div>
                <div className="card"><Badge plain>source: platform rules</Badge><div style={{ marginTop: 8 }}><CompletenessPanel c={w.completeness} /></div><div style={{ marginTop: 12 }}><Checklist items={w.checklist} compact /></div></div>
                <div className="card"><Badge plain>source: automated checks</Badge><div style={{ marginTop: 8 }}><ChecksPanel checks={w.checks} audience="insurer" /></div></div>
                {w.decisions.length > 0 && (
                  <div className="card">
                    <h3>{lang === "zh" ? "结论记录" : "Decisions"}</h3>
                    {w.decisions.map((d) => <div key={d.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}><div className="row"><StatusBadge status={d.outcome} /><span className="small muted">{d.decided_by} · {fmtDateTime(d.created_at, lang)}</span>{d.approved_amount != null && <span>{money(d.approved_amount, d.currency, lang)}</span>}</div><p style={{ margin: "4px 0" }}>{d.reason}</p><div className="row">{d.clause_refs.map((r) => <CitationChip key={r} citation={`${w.policy?.policy_number} §${r}`} />)}</div></div>)}
                  </div>
                )}
                {w.information_requests.length > 0 && (
                  <div className="card">
                    <h3>{t("information_requests")}</h3>
                    {w.information_requests.map((r) => <div key={r.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}><div className="row between"><StatusBadge status={r.status === "open" ? "warn" : "ok"} label={r.status} /><span className="small muted">{r.requested_by} · {fmtDateTime(r.created_at, lang)}{r.due_date ? ` · due ${fmtDate(r.due_date, lang)}` : ""}</span></div><p style={{ whiteSpace: "pre-wrap", margin: "4px 0" }}>{r.message}</p>{r.response_text && <div className="alert ok small"><b>{lang === "zh" ? "客户回复：" : "Customer response:"}</b> {r.response_text}</div>}</div>)}
                  </div>
                )}
                <div className="card">
                  <h3>{lang === "zh" ? "内部备注" : "Internal notes"} <span className="small muted">({lang === "zh" ? "客户不可见" : "not visible to the customer"})</span></h3>
                  {w.internal_notes.map((n) => <div key={n.id} className="small" style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}><b>{n.author}</b> · <span className="muted">{fmtDateTime(n.created_at, lang)}</span><div>{n.text}</div></div>)}
                  <div className="row" style={{ marginTop: 8 }}><input className="input" placeholder={t("internal_note")} value={note} onChange={(e) => setNote(e.target.value)} /><button className="btn sm" disabled={!note.trim()} onClick={() => act(() => api.post(`/api/insurer/claims/${c.id}/notes`, { text: note }).then(() => setNote("")))}>{t("save")}</button></div>
                </div>
              </div>
            )}
            {tab === "documents" && (
              <div className="card">
                {w.documents.length === 0 && <div className="muted">—</div>}
                {w.documents.map((d) => (
                  <div key={d.id} style={{ padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
                    <div className="row between"><span><a href={`/api/documents/${d.id}/download`} target="_blank" rel="noreferrer">{d.filename}</a> <Badge plain>{d.doc_type}</Badge> {d.checklist_item_key && <span className="small muted">{d.checklist_item_key}</span>}</span><span className="small muted">{fmtDateTime(d.created_at, lang)} · {d.extraction_status} · sha256 {d.sha256.slice(0, 10)}</span></div>
                    {d.injection_flags.length > 0 && <div className="alert warn small" style={{ marginTop: 6 }}><Icon name="warning" size={13} /> {lang === "zh" ? "文件中含有类似指令的文字，已被助手忽略：" : "This file contains instruction like text, ignored by the assistant:"} {d.injection_flags.map((f) => f.type).join(", ")}</div>}
                    {d.extracted_excerpt && <div className="small muted" style={{ marginTop: 4, whiteSpace: "pre-wrap" }}>{d.extracted_excerpt.slice(0, 300)}{d.extracted_excerpt.length > 300 ? "…" : ""}</div>}
                  </div>
                ))}
              </div>
            )}
            {tab === "messages" && (
              <div className="card">
                {w.communications.length === 0 && <div className="muted">—</div>}
                {w.communications.map((m) => <div key={m.id} style={{ padding: "10px 0", borderBottom: "1px solid var(--line)" }}><div className="row between"><strong>{m.direction === "outbound" ? "← " + (lang === "zh" ? "客户" : "customer") : "→ " + (lang === "zh" ? "本司" : "us")}: {m.subject}</strong><span className="small muted">{fmtDateTime(m.sent_at || m.created_at, lang)}</span></div><div className="small" style={{ whiteSpace: "pre-wrap", color: "var(--ink-2)" }}>{m.body}</div></div>)}
              </div>
            )}
            {tab === "audit" && (
              <div className="grid cols-2">
                <div className="card"><h3>{t("timeline")}</h3><ul className="timeline">{[...w.timeline].reverse().map((e) => <li key={e.id}><div className="when">{fmtDateTime(e.at, lang)} · {e.actor_type} · {e.event_type}</div>{e.summary}</li>)}</ul></div>
                <div className="card"><h3>{t("audit")}</h3><table className="table"><tbody>{w.audit.map((a) => <tr key={a.seq}><td className="mono small">#{a.seq}</td><td className="small">{a.action}<div className="muted">{a.actor_type} · {a.outcome}</div></td><td className="small muted">{fmtDateTime(a.created_at, lang)}<div className="mono">{a.hash}</div></td></tr>)}</tbody></table>{w.submissions.length > 0 && <div className="small muted" style={{ marginTop: 8 }}>{w.submissions.map((s) => <div key={s.id}>{s.adapter} · {s.status}{s.mock ? " (mock)" : ""} · {s.insurer_reference || s.error}</div>)}</div>}</div>
              </div>
            )}
          </div>
          <aside className="stack">
            <div className="card">
              <h3>{lang === "zh" ? "操作" : "Actions"}</h3>
              <div className="stack" style={{ gap: 8 }}>
                <div className="row">
                  <select className="select" value={c.assigned_handler_id || ""} onChange={(e) => act(() => api.post(`/api/insurer/claims/${c.id}/assign`, { handler_id: e.target.value || null }), lang === "zh" ? "已分配" : "Assigned")}>
                    <option value="">{t("unassigned")}</option>{team.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                  <button className="btn sm" onClick={() => act(() => api.post(`/api/insurer/claims/${c.id}/assign`, { handler_id: "me" }), lang === "zh" ? "已分配给你" : "Assigned to you")}>{t("assign_me")}</button>
                </div>
                {w.allowed_statuses.length > 0 && (
                  <div className="row">{w.allowed_statuses.map((s) => <button key={s} className="btn sm" onClick={() => act(() => api.post(`/api/insurer/claims/${c.id}/status`, { status: s }), s)}>{s.replace(/_/g, " ")}</button>)}</div>
                )}
              </div>
            </div>
            {["submitted", "acknowledged", "under_review", "reopened"].includes(c.status) && (
              <div className="card">
                <h3>{t("request_info")}</h3>
                <div className="stack" style={{ gap: 8 }}>
                  <textarea className="textarea" placeholder={lang === "zh" ? "给客户的信息（会原样发送）" : "Message to the customer (sent as written)"} value={req.message} onChange={(e) => setReq({ ...req, message: e.target.value })} />
                  <textarea className="textarea" style={{ minHeight: 60 }} placeholder={lang === "zh" ? "所需材料，每行一项" : "Requested items, one per line"} value={req.items} onChange={(e) => setReq({ ...req, items: e.target.value })} />
                  <input className="input" type="date" value={req.due_date} onChange={(e) => setReq({ ...req, due_date: e.target.value })} />
                  <button className="btn primary sm" disabled={req.message.trim().length < 10} onClick={() => act(() => api.post(`/api/insurer/claims/${c.id}/request-information`, { message: req.message, requested_items: req.items.split("\n").map((s) => s.trim()).filter(Boolean), due_date: req.due_date || null }).then(() => setReq({ message: "", items: "", due_date: "" })), lang === "zh" ? "已发送给客户" : "Sent to the customer")}>{t("send")}</button>
                </div>
              </div>
            )}
            {w.can_decide && (
              <div className="card">
                <h3>{t("record_decision")}</h3>
                <div className="stack" style={{ gap: 8 }}>
                  <select className="select" value={dec.outcome} onChange={(e) => setDec({ ...dec, outcome: e.target.value })}><option value="approved">approved</option><option value="partially_approved">partially approved</option><option value="declined">declined</option></select>
                  <textarea className="textarea" placeholder={lang === "zh" ? "书面理由（至少20字，客户可见）" : "Written reason (min 20 chars, shown to the customer)"} value={dec.reason} onChange={(e) => setDec({ ...dec, reason: e.target.value })} />
                  <input className="input" placeholder={lang === "zh" ? "依据条款，如 2.6, 5.1" : "Clauses relied on, e.g. 2.6, 5.1"} value={dec.clause_refs} onChange={(e) => setDec({ ...dec, clause_refs: e.target.value })} />
                  {dec.outcome !== "declined" && <input className="input" type="number" placeholder={lang === "zh" ? "批准金额" : "Approved amount"} value={dec.approved_amount} onChange={(e) => setDec({ ...dec, approved_amount: e.target.value })} />}
                  <div className="small muted">{lang === "zh" ? "结论由你本人作出并记录在审计日志中；助手不能作出结论。" : "The decision is yours and is written to the audit log. The copilot cannot decide."}</div>
                  <button className="btn primary sm" disabled={dec.reason.trim().length < 20} onClick={() => act(() => api.post(`/api/insurer/claims/${c.id}/decision`, { outcome: dec.outcome, reason: dec.reason, clause_refs: dec.clause_refs.split(",").map((s) => s.trim()).filter(Boolean), approved_amount: dec.approved_amount === "" ? null : Number(dec.approved_amount) }).then(() => setDec({ outcome: "approved", reason: "", clause_refs: "", approved_amount: "" })), lang === "zh" ? "结论已记录" : "Decision recorded")}>{t("record_decision")}</button>
                </div>
              </div>
            )}
            <div className="card" style={{ background: "var(--teal-soft)" }}>
              <div className="card-head"><h3>{t("copilot")}</h3><Badge plain>tier ≤ 1</Badge></div>
              <div className="stack" style={{ gap: 8, maxHeight: 420, overflow: "auto" }}>
                {copilotMsgs.length === 0 && <div className="small muted">{lang === "zh" ? "总结案件、找出缺失材料、起草补充材料请求。不作结论。" : "Summarise the case, find what is missing, draft an information request. It never decides."}</div>}
                {copilotMsgs.map((m) => (
                  <div key={m.id} className={`msg ${m.role}`} style={{ maxWidth: "100%" }}>
                    {m.role === "user" ? <div className="small">{m.content}</div> : <div className="bubble" style={{ background: "var(--bg-elev)" }}><Markdown text={m.content} /><MessageBlocks blocks={m.blocks || []} />{m.run_id && <button className="btn ghost sm" style={{ padding: 0 }} onClick={() => setShowRun(showRun === m.run_id ? null : m.run_id)}>{t("why_this_answer")}</button>}{showRun === m.run_id && copilotRuns[m.run_id!] && <RunTransparency run={copilotRuns[m.run_id!]} lang={lang} />}</div>}
                  </div>
                ))}
                {copilotBusy && <Spinner />}
              </div>
              <div className="suggestions" style={{ margin: "8px 0" }}>{(lang === "zh" ? ["总结这个案件", "缺少什么材料？", "起草补充材料请求"] : ["Summarise this claim", "What is missing?", "Draft a request for the missing items"]).map((s) => <button key={s} onClick={() => copilot(s)}>{s}</button>)}</div>
              <div className="row"><input className="input" value={copilotText} onChange={(e) => setCopilotText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && copilot(copilotText)} placeholder="…" /><button className="btn sm primary icon" disabled={copilotBusy} aria-label={t("send")} onClick={() => copilot(copilotText)}><Icon name="send" size={14} /></button></div>
            </div>
            <span className="sr-only">{pick({ en: "" })}</span>
          </aside>
        </div>
      </CitationProvider>
    </InsurerShell>
  );
}
