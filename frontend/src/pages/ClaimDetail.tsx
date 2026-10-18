import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApprovalCard, Checklist, CompletenessPanel, CoverageViewPanel, DraftCard, type Approval, type ChecklistItem, type Completeness, type CoverageView } from "../components/Blocks";
import { CustomerShell } from "../components/Layout";
import { CitationProvider, CitationChip, type ClauseRef } from "../components/Markdown";
import { Badge, Field, Modal, Spinner, StatusBadge, useToast } from "../components/ui";
import { api, errorMessage, postBlob } from "../lib/api";
import { fmtDate, fmtDateTime, money } from "../lib/format";
import { useLang } from "../lib/i18n";
import type { PolicyCard } from "./Overview";

interface Detail {
  claim: { id: string; reference: string; status: string; status_label: string; status_source: string; status_updated_at: string; incident_type: string; incident_date: string | null; incident_time: string | null; location: string | null; description: string; items_involved: { label: string; value?: number }[]; estimated_loss: number | null; loss_unknown: boolean; currency: string; third_party_involved: boolean | null; authorities_involved: boolean | null; authority_reference: string | null; actions_taken: string | null; safety_concern: boolean; insurer_reference: string | null; submitted_at: string | null; editable: boolean; language: string; policy_id: string | null };
  policy: PolicyCard | null;
  insurer: { name: string; name_zh?: string; claims_email: string; integration_mode: string } | null;
  handler: { name: string } | null;
  checklist: ChecklistItem[];
  completeness: Completeness;
  coverage_view: CoverageView;
  documents: { id: string; filename: string; doc_type: string; checklist_item_key: string | null; submitted_at: string | null; injection_flags: unknown[]; created_at: string; sha256: string }[];
  information_requests: { id: string; message: string; requested_items: string[]; due_date: string | null; status: string; response_text: string | null; created_at: string }[];
  communications: { id: string; direction: string; kind: string; subject: string; body: string; recipient: string | null; status: string; sent_at: string | null; created_at: string; claim_id?: string | null }[];
  decision: { outcome: string; reason: string; clause_refs: string[]; approved_amount: number | null; currency: string; decided_at: string } | null;
  submissions: { id: string; status: string; mode: string; adapter: string; error: string | null; insurer_reference: string | null; mock: boolean; created_at: string }[];
  pending_approvals: Approval[];
  timeline: { id: string; at: string; actor_type: string; event_type: string; summary: string }[];
}

export default function ClaimDetailPage() {
  const { id } = useParams();
  const { t, lang, pick } = useLang();
  const toast = useToast();
  const [d, setD] = useState<Detail | null>(null);
  const [uploadFor, setUploadFor] = useState<ChecklistItem | null | "any">(null);
  const [docType, setDocType] = useState("other");
  const [approval, setApproval] = useState<Approval | null>(null);
  const [respondFor, setRespondFor] = useState<string | null>(null);
  const [respText, setRespText] = useState("");
  const [respDocs, setRespDocs] = useState<string[]>([]);
  const [editing, setEditing] = useState(false);
  const [edit, setEdit] = useState<Record<string, unknown>>({});
  const [markSent, setMarkSent] = useState(false);
  const [sentForm, setSentForm] = useState({ channel: "email", reference: "" });
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.get<Detail>(`/api/claims/${id}`).then(setD), [id]);
  useEffect(() => { load(); }, [load]);
  if (!d) return <CustomerShell><Spinner /></CustomerShell>;
  const c = d.claim;
  const clauses: ClauseRef[] = [...(d.coverage_view.covering_clauses || []), ...(d.coverage_view.possible_exclusions || []), ...(d.coverage_view.conditions || []), ...(d.coverage_view.financial_terms || []), ...d.checklist.flatMap((i) => i.citations as ClauseRef[])];
  const upload = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("doc_type", uploadFor && uploadFor !== "any" ? (uploadFor.doc_types[0] || "other") : docType);
    form.append("claim_id", c.id);
    if (uploadFor && uploadFor !== "any") form.append("checklist_item_key", uploadFor.key);
    setBusy(true);
    try {
      await api.upload("/api/documents/upload", form);
      setUploadFor(null);
      toast(lang === "zh" ? "已上传" : "Uploaded");
      load();
    } catch (e) {
      toast(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const submit = async () => {
    try {
      const r = await api.post<{ approval: Approval }>(`/api/claims/${c.id}/submit`);
      setApproval(r.approval);
      load();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const respond = async () => {
    if (!respondFor) return;
    try {
      const r = await api.post<{ approval: Approval }>(`/api/claims/${c.id}/information-requests/${respondFor}/respond`, { response_text: respText, document_ids: respDocs });
      setApproval(r.approval);
      setRespondFor(null);
      setRespText("");
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const saveEdit = async () => {
    try {
      await api.patch(`/api/claims/${c.id}`, { fields: edit });
      setEditing(false);
      load();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const doMarkSent = async () => {
    try {
      await api.post(`/api/claims/${c.id}/mark-sent`, sentForm);
      setMarkSent(false);
      load();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const pending = approval && approval.status === "pending" ? approval : d.pending_approvals[0] ? { ...d.pending_approvals[0], token: approval?.token } : approval;
  const preSubmission = ["draft", "information_required", "ready_for_review", "awaiting_customer_approval", "submission_failed"].includes(c.status);
  const openRequests = d.information_requests.filter((r) => r.status === "open");
  const insurerName = d.insurer ? (lang === "zh" && d.insurer.name_zh ? d.insurer.name_zh : d.insurer.name) : null;
  return (
    <CustomerShell title={c.reference} subtitle={`${c.incident_type.replace(/_/g, " ")} · ${d.policy ? `${d.policy.provider_name} ${d.policy.product_name} (${d.policy.policy_number})` : lang === "zh" ? "未关联保单" : "no policy linked"}`} actions={<Link className="btn primary" to={`/assistant?claim=${c.id}`}>{lang === "zh" ? "就此理赔询问助手" : "Ask the assistant about this claim"}</Link>}>
      <CitationProvider clauses={clauses}>
        <div className="card" style={{ marginBottom: 16, display: "flex", gap: 24, alignItems: "center", flexWrap: "wrap" }}>
          <div><div className="small muted">{t("last_known")}</div><StatusBadge status={c.status} label={c.status_label} /> <span className="small muted">{t("from")} {c.status_source} · {fmtDateTime(c.status_updated_at, lang)}</span></div>
          {insurerName && <div><div className="small muted">{t("provider")}</div>{insurerName}{c.insurer_reference && <span className="mono small"> · {c.insurer_reference}</span>}</div>}
          {d.handler && <div><div className="small muted">{t("handler")}</div>{d.handler.name}</div>}
          <div><div className="small muted">{lang === "zh" ? "预计损失" : "Estimated loss"}</div>{c.loss_unknown ? (lang === "zh" ? "不确定" : "unknown") : money(c.estimated_loss, c.currency, lang)}</div>
          <div style={{ marginLeft: "auto" }} className="row">
            {preSubmission && c.editable && <button className="btn sm" onClick={() => { setEdit({ incident_date: c.incident_date, incident_time: c.incident_time, location: c.location, description: c.description, estimated_loss: c.estimated_loss, authority_reference: c.authority_reference, actions_taken: c.actions_taken, authorities_involved: c.authorities_involved, third_party_involved: c.third_party_involved }); setEditing(true); }}>{t("edit")}</button>}
            {preSubmission && d.insurer && <button className="btn primary" disabled={!d.completeness.ready || !!pending} onClick={submit} title={!d.completeness.ready ? (lang === "zh" ? "先完成必需项" : "Finish the required items first") : ""}>{t("submit_claim")}…</button>}
            {preSubmission && !d.insurer && <button className="btn primary" disabled={!d.completeness.ready} onClick={() => setMarkSent(true)}>{t("mark_sent")}</button>}
            <button className="btn sm" onClick={async () => { try { const blob = await postBlob(`/api/claims/${c.id}/pack/pdf`); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${c.reference}-claim-pack.pdf`; a.click(); load(); } catch (e) { toast(errorMessage(e)); } }}>{t("assisted_pack")}</button>
          </div>
        </div>
        {c.safety_concern && <div className="alert bad" style={{ marginBottom: 14 }}>{lang === "zh" ? "你表示可能有人受伤或处于危险中。请先联系急救或报警；理赔可以稍后处理。" : "You indicated someone may be hurt or in danger. Please contact the emergency services first; the claim can wait."}</div>}
        {pending && <div style={{ marginBottom: 14 }}><ApprovalCard approval={pending} onDone={() => { setApproval(null); load(); }} /></div>}
        {!d.insurer && preSubmission && <div className="alert info" style={{ marginBottom: 14 }}>{lang === "zh" ? "这份保单没有连接到平台上的保险公司，因此采用辅助提交：下载材料包，通过保险公司自己的渠道发送，然后记录你已发送。" : "This policy is not connected to an insurer on the platform, so submission is assisted: download the pack, send it through the insurer's own channel, then record that you sent it."}</div>}
        {c.status === "submission_failed" && <div className="alert bad" style={{ marginBottom: 14 }}>{lang === "zh" ? "上次提交失败，没有任何内容发送到保险公司。你可以重试。" : "The last submission attempt failed; nothing reached the insurer. You can try again."} {d.submissions[0]?.error && <span className="mono small">({d.submissions[0].error})</span>}</div>}

        {openRequests.length > 0 && (
          <div className="card" style={{ marginBottom: 16, borderColor: "var(--warn)" }}>
            <h3>{t("information_requests")}</h3>
            {openRequests.map((r) => (
              <div key={r.id} className="stack" style={{ gap: 8 }}>
                <p style={{ whiteSpace: "pre-wrap" }}>{r.message}</p>
                {r.requested_items.length > 0 && <ul style={{ margin: 0, paddingLeft: 18 }}>{r.requested_items.map((i) => <li key={i}>{i}</li>)}</ul>}
                <div className="small muted">{r.due_date ? `${lang === "zh" ? "要求在" : "Requested by"} ${fmtDate(r.due_date, lang)}` : ""} · {fmtDateTime(r.created_at, lang)}</div>
                {respondFor === r.id ? (
                  <div className="stack">
                    <textarea className="textarea" placeholder={lang === "zh" ? "你的回复" : "Your response"} value={respText} onChange={(e) => setRespText(e.target.value)} />
                    <div className="small">{lang === "zh" ? "附上的文件：" : "Attach documents:"} {d.documents.filter((x) => !x.submitted_at).map((x) => <label key={x.id} style={{ marginRight: 10 }}><input type="checkbox" checked={respDocs.includes(x.id)} onChange={(e) => setRespDocs(e.target.checked ? [...respDocs, x.id] : respDocs.filter((y) => y !== x.id))} /> {x.filename}</label>)}{d.documents.filter((x) => !x.submitted_at).length === 0 && <span className="muted">{lang === "zh" ? "（先上传新文件）" : "(upload new documents first)"}</span>}</div>
                    <div className="row"><button className="btn primary" disabled={!respText.trim()} onClick={respond}>{t("send")}…</button><button className="btn" onClick={() => setRespondFor(null)}>{t("cancel")}</button><button className="btn sm" onClick={() => setUploadFor("any")}>{t("upload")}</button></div>
                  </div>
                ) : <div className="row"><button className="btn primary sm" onClick={() => setRespondFor(r.id)}>{t("respond")}</button><button className="btn sm" onClick={() => setUploadFor("any")}>{t("upload")}</button></div>}
              </div>
            ))}
          </div>
        )}

        {d.decision && (
          <div className="card" style={{ marginBottom: 16, borderColor: d.decision.outcome === "declined" ? "var(--bad)" : "var(--ok)" }}>
            <div className="card-head"><h3>{t("decision")}</h3><StatusBadge status={d.decision.outcome} /></div>
            <p>{d.decision.reason}</p>
            <div className="row small muted">{d.decision.clause_refs.length > 0 && <span>{lang === "zh" ? "依据条款：" : "Clauses relied on:"} {d.decision.clause_refs.map((r) => <CitationChip key={r} citation={`${d.policy?.policy_number} §${r}`} />)}</span>}{d.decision.approved_amount != null && <span>{lang === "zh" ? "批准金额" : "Approved"}: {money(d.decision.approved_amount, d.decision.currency, lang)}</span>}<span>{fmtDateTime(d.decision.decided_at, lang)}</span></div>
            <Link className="btn sm" style={{ marginTop: 8 }} to={`/assistant?claim=${c.id}&q=${encodeURIComponent(lang === "zh" ? "解释这个理赔结论，并帮我准备复核申请" : "Explain this decision and help me ask for a review")}`}>{t("explain_decision")}</Link>
          </div>
        )}

        <div className="grid cols-2">
          <div className="stack">
            <div className="card"><CoverageViewPanel v={d.coverage_view} /></div>
            <div className="card">
              <h3>{t("incident")}</h3>
              <dl className="kv">
                <dt>{lang === "zh" ? "日期" : "Date"}</dt><dd>{fmtDate(c.incident_date, lang)} {c.incident_time || ""}</dd>
                <dt>{lang === "zh" ? "地点" : "Location"}</dt><dd>{c.location || "—"}</dd>
                <dt>{lang === "zh" ? "经过" : "What happened"}</dt><dd style={{ whiteSpace: "pre-wrap" }}>{c.description}</dd>
                <dt>{lang === "zh" ? "涉及物品" : "Items"}</dt><dd>{c.items_involved.length ? c.items_involved.map((i, k) => <div key={k}>{i.label}{i.value != null ? ` · ${money(i.value, c.currency, lang)}` : ""}</div>) : "—"}</dd>
                <dt>{lang === "zh" ? "报案" : "Authorities"}</dt><dd>{c.authorities_involved ? (lang === "zh" ? "已报案" : "Reported") : c.authorities_involved === false ? (lang === "zh" ? "未报案" : "Not reported") : "—"}{c.authority_reference ? ` · ${c.authority_reference}` : ""}</dd>
                <dt>{lang === "zh" ? "已采取措施" : "Actions taken"}</dt><dd style={{ whiteSpace: "pre-wrap" }}>{c.actions_taken || "—"}</dd>
              </dl>
            </div>
            <div className="card">
              <h3>{t("timeline")}</h3>
              <ul className="timeline">{[...d.timeline].reverse().map((e) => <li key={e.id}><div className="when">{fmtDateTime(e.at, lang)} · {e.actor_type}</div>{e.summary}</li>)}</ul>
            </div>
          </div>
          <div className="stack">
            <div className="card">
              <CompletenessPanel c={d.completeness} />
            </div>
            <div className="card">
              <div className="card-head"><h3>{t("checklist")}</h3>{preSubmission && <button className="btn sm" onClick={() => setUploadFor("any")}>{t("upload")}</button>}</div>
              <Checklist items={d.checklist} onUpload={preSubmission || openRequests.length > 0 ? (i) => setUploadFor(i) : undefined} />
            </div>
            <div className="card">
              <h3>{t("documents")} <span className="muted small">({d.documents.length})</span></h3>
              {d.documents.length === 0 ? <div className="small muted">—</div> : d.documents.map((x) => (
                <div key={x.id} className="row between" style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                  <span><a href={`/api/documents/${x.id}/download`} target="_blank" rel="noreferrer">{x.filename}</a> <span className="small muted">{x.doc_type}{x.checklist_item_key ? ` · ${x.checklist_item_key}` : ""}</span>{x.injection_flags?.length ? <Badge tone="warn" plain>⚠ {lang === "zh" ? "含指令文字" : "instruction text"}</Badge> : null}</span>
                  <span className="small muted">{x.submitted_at ? (lang === "zh" ? "已发送" : "shared") : lang === "zh" ? "未发送" : "not shared"}</span>
                </div>
              ))}
            </div>
            <div className="card">
              <h3>{t("messages")}</h3>
              {d.communications.length === 0 && <div className="small muted">—</div>}
              {d.communications.map((m) => m.status === "draft" ? <DraftCard key={m.id} comm={m} onSent={load} /> : (
                <div key={m.id} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
                  <div className="row between"><strong>{m.direction === "inbound" ? "← " : "→ "}{m.subject}</strong><span className="small muted">{fmtDateTime(m.sent_at || m.created_at, lang)}</span></div>
                  <div className="small" style={{ whiteSpace: "pre-wrap", color: "var(--ink-2)" }}>{m.body}</div>
                </div>
              ))}
              <div className="row" style={{ marginTop: 8 }}>
                <Link className="btn sm" to={`/assistant?claim=${c.id}&q=${encodeURIComponent(lang === "zh" ? "帮我写一封询问理赔进度的信" : "Draft a message asking the insurer for an update on this claim")}`}>{lang === "zh" ? "让助手起草信件" : "Draft with the assistant"}</Link>
              </div>
            </div>
            {d.submissions.length > 0 && (
              <div className="card">
                <h3>{t("submissions")}</h3>
                {d.submissions.map((s) => <div key={s.id} className="row between small" style={{ padding: "4px 0" }}><span><StatusBadge status={s.status === "succeeded" ? "ok" : "failed"} label={s.status} /> {s.mode} · {s.adapter}{s.mock ? ` (${t("mock_ack")})` : ""}</span><span className="muted">{s.insurer_reference || s.error || ""} · {fmtDateTime(s.created_at, lang)}</span></div>)}
              </div>
            )}
          </div>
        </div>

        {uploadFor && (
          <Modal title={uploadFor === "any" ? t("upload") : pick(uploadFor.label)} onClose={() => setUploadFor(null)}>
            <div className="stack">
              {uploadFor !== "any" && <div className="alert info small"><b>{t("why_needed")}:</b> {pick(uploadFor.reason)}<br /><b>{t("how_to_get")}:</b> {pick(uploadFor.how)}</div>}
              {uploadFor === "any" && <Field label={lang === "zh" ? "文件类型" : "Document type"}><select className="select" value={docType} onChange={(e) => setDocType(e.target.value)}>{["photo", "receipt", "invoice", "estimate", "police_report", "correspondence", "medical", "proof_of_ownership", "other"].map((x) => <option key={x} value={x}>{x}</option>)}</select></Field>}
              <input type="file" accept=".pdf,.txt,.md,.png,.jpg,.jpeg,.webp" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} disabled={busy} />
              <div className="small muted">{lang === "zh" ? "PDF、PNG、JPEG、WEBP 或文本，最大 10 MB。原件会被完整保留，不会被修改。" : "PDF, PNG, JPEG, WEBP or text, up to 10 MB. The original file is kept unchanged."}</div>
            </div>
          </Modal>
        )}
        {editing && (
          <Modal title={t("edit")} onClose={() => setEditing(false)}>
            <div className="grid cols-2">
              <Field label={lang === "zh" ? "日期" : "Date"}><input className="input" type="date" value={String(edit.incident_date || "")} onChange={(e) => setEdit({ ...edit, incident_date: e.target.value })} /></Field>
              <Field label={lang === "zh" ? "时间" : "Time"}><input className="input" type="time" value={String(edit.incident_time || "")} onChange={(e) => setEdit({ ...edit, incident_time: e.target.value })} /></Field>
              <Field label={lang === "zh" ? "地点" : "Location"}><input className="input" value={String(edit.location || "")} onChange={(e) => setEdit({ ...edit, location: e.target.value })} /></Field>
              <Field label={lang === "zh" ? "预计损失" : "Estimated loss"}><input className="input" type="number" value={edit.estimated_loss == null ? "" : String(edit.estimated_loss)} onChange={(e) => setEdit({ ...edit, estimated_loss: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
              <Field label={lang === "zh" ? "报案编号" : "Police / authority reference"}><input className="input" value={String(edit.authority_reference || "")} onChange={(e) => setEdit({ ...edit, authority_reference: e.target.value, authorities_involved: e.target.value ? true : edit.authorities_involved })} /></Field>
              <Field label={lang === "zh" ? "已报案？" : "Reported to authorities?"}><select className="select" value={edit.authorities_involved == null ? "" : edit.authorities_involved ? "yes" : "no"} onChange={(e) => setEdit({ ...edit, authorities_involved: e.target.value === "" ? null : e.target.value === "yes" })}><option value="">—</option><option value="yes">{lang === "zh" ? "是" : "Yes"}</option><option value="no">{lang === "zh" ? "否" : "No"}</option></select></Field>
            </div>
            <Field label={lang === "zh" ? "经过" : "Description"}><textarea className="textarea" value={String(edit.description || "")} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
            <Field label={lang === "zh" ? "已采取措施" : "Actions taken"}><textarea className="textarea" value={String(edit.actions_taken || "")} onChange={(e) => setEdit({ ...edit, actions_taken: e.target.value })} /></Field>
            <div className="row" style={{ marginTop: 12 }}><button className="btn primary" onClick={saveEdit}>{t("save")}</button><button className="btn" onClick={() => setEditing(false)}>{t("cancel")}</button></div>
          </Modal>
        )}
        {markSent && (
          <Modal title={t("mark_sent")} onClose={() => setMarkSent(false)}>
            <div className="stack">
              <div className="alert info small">{lang === "zh" ? "这会记录你已通过保险公司的渠道发送材料包。状态显示为“已提交”，但来源标注为你本人，直到保险公司确认。" : "This records that you sent the pack through the insurer's own channel. The status will show as submitted with you as the source until the insurer confirms."}</div>
              <Field label={lang === "zh" ? "渠道" : "Channel"}><select className="select" value={sentForm.channel} onChange={(e) => setSentForm({ ...sentForm, channel: e.target.value })}>{["email", "portal", "post", "phone", "app"].map((x) => <option key={x}>{x}</option>)}</select></Field>
              <Field label={lang === "zh" ? "保险公司编号（如有）" : "Insurer reference (if given)"}><input className="input" value={sentForm.reference} onChange={(e) => setSentForm({ ...sentForm, reference: e.target.value })} /></Field>
              <div className="row"><button className="btn primary" onClick={doMarkSent}>{t("save")}</button><button className="btn" onClick={() => setMarkSent(false)}>{t("cancel")}</button></div>
            </div>
          </Modal>
        )}
      </CitationProvider>
    </CustomerShell>
  );
}
