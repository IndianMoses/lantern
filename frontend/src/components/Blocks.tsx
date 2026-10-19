import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../lib/api";
import { fmtDateTime, money, VERDICT_LABEL } from "../lib/format";
import { useLang } from "../lib/i18n";
import { CitationChip, RichText, type ClauseRef } from "./Markdown";
import { Icon } from "./Icons";
import { Badge, Meter, StatusBadge, useToast } from "./ui";

// ------------------------------------------------------------------ types
export interface ChecklistItem {
  key: string;
  kind: "document" | "information" | "action";
  status: "required" | "recommended" | "optional" | "not_applicable" | "awaiting_insurer";
  label: Record<string, string>;
  reason: Record<string, string>;
  how: Record<string, string>;
  doc_types: string[];
  citations: { citation: string; clause_ref: string; title?: string }[];
  satisfied: boolean;
  satisfied_by: string[];
}
export interface Completeness {
  progress: number;
  ready: boolean;
  missing_fields: string[];
  missing_required_documents: string[];
  missing_recommended_documents: string[];
  outstanding_actions: string[];
  outstanding_information: string[];
  conflicts: { code: string; en: string; zh: string }[];
  needs_human_review: string[];
  note: Record<string, string>;
}
export interface CoverageView {
  verdict: string;
  summary: Record<string, string>;
  covering_clauses: ClauseRef[];
  possible_exclusions: ClauseRef[];
  conditions: ClauseRef[];
  financial_terms: ClauseRef[];
  limit_notes?: { en: string; zh: string }[];
  disclaimer?: Record<string, string>;
  policy_name?: string;
}
export interface Finding {
  need: string;
  label: Record<string, string>;
  status: string;
  severity: string;
  explanation: Record<string, string>;
  suggested_action: Record<string, string>;
  triggered_by: string;
  evidence: ClauseRef[];
  policies: string[];
}
export interface Approval {
  id: string;
  action_type: string;
  summary: string;
  consequences: { en: string; zh: string }[];
  payload: Record<string, unknown>;
  status: string;
  expires_at: string;
  token?: string;
  result?: Record<string, unknown>;
}

// ------------------------------------------------------------- checklist
export function Checklist({ items, onUpload, compact }: { items: ChecklistItem[]; onUpload?: (item: ChecklistItem) => void; compact?: boolean }) {
  const { t, pick } = useLang();
  const [open, setOpen] = useState<string | null>(null);
  const shown = compact ? items.filter((i) => i.status !== "optional" || i.satisfied) : items;
  return (
    <ul className="checklist" style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {shown.map((i) => (
        <li key={i.key}>
          <span className={`tick ${i.satisfied ? "done" : ""}`}>{i.satisfied ? <Icon name="check" size={12} /> : ""}</span>
          <div>
            <div className="row" style={{ gap: 8 }}>
              <strong>{pick(i.label)}</strong>
              <Badge tone={i.status === "required" ? "bad" : i.status === "recommended" ? "warn" : ""} plain>{t(i.status) || i.status}</Badge>
              <span className="small muted">{i.kind}</span>
              {i.citations.map((c) => (
                <CitationChip key={c.citation} citation={c.citation} />
              ))}
            </div>
            <button className="btn ghost sm" style={{ padding: "2px 0" }} onClick={() => setOpen(open === i.key ? null : i.key)}>
              <Icon name={open === i.key ? "minus" : "plus"} size={13} /> {t("why_needed")} · {t("how_to_get")}
            </button>
            {open === i.key && (
              <div className="small" style={{ color: "var(--ink-2)", marginTop: 4 }}>
                <div><b>{t("why_needed")}:</b> {pick(i.reason)}</div>
                <div><b>{t("how_to_get")}:</b> {pick(i.how)}</div>
              </div>
            )}
          </div>
          <div>{onUpload && i.kind === "document" && !i.satisfied && <button className="btn sm" onClick={() => onUpload(i)}>{t("upload")}</button>}</div>
        </li>
      ))}
    </ul>
  );
}

export function CompletenessPanel({ c }: { c: Completeness }) {
  const { t, pick, lang } = useLang();
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row between">
        <strong>{t("completeness")}</strong>
        <span>{c.progress}% {c.ready ? <Badge tone="ok">{lang === "zh" ? "可提交" : "ready"}</Badge> : <Badge tone="warn">{lang === "zh" ? "未完成" : "not ready"}</Badge>}</span>
      </div>
      <Meter value={c.progress} ok={c.ready} />
      {(c.missing_fields.length > 0 || c.missing_required_documents.length > 0 || c.outstanding_actions.length > 0 || c.outstanding_information.length > 0) && (
        <div className="small">
          {lang === "zh" ? "仍缺：" : "Still missing: "}
          {[...c.missing_fields, ...c.missing_required_documents, ...c.outstanding_information, ...c.outstanding_actions].map((k) => (
            <code key={k} style={{ marginRight: 4 }}>{k}</code>
          ))}
        </div>
      )}
      {c.conflicts.map((x) => (
        <div key={x.code} className={`alert ${x.code === "safety_first" ? "bad" : "warn"}`}>{pick(x)}</div>
      ))}
      <div className="small muted">{pick(c.note)}</div>
    </div>
  );
}

export function CoverageViewPanel({ v }: { v: CoverageView }) {
  const { t, pick, lang } = useLang();
  const tone = v.verdict.startsWith("appears_covered") ? (v.verdict.includes("exclusion") ? "warn" : "ok") : v.verdict === "not_addressed" || v.verdict === "no_policy" ? "" : "warn";
  const label = VERDICT_LABEL[v.verdict]?.[lang] || v.verdict;
  const group = (title: string, items: ClauseRef[]) =>
    items && items.length > 0 ? (
      <div>
        <div className="small muted" style={{ marginBottom: 4 }}>{title}</div>
        <div className="row" style={{ gap: 6 }}>
          {items.map((c) => (
            <span key={c.citation} className="row" style={{ gap: 4 }}>
              <CitationChip citation={c.citation} />
              <span className="small">{c.title}</span>
            </span>
          ))}
        </div>
      </div>
    ) : null;
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row between">
        <strong>{t("coverage_reading")}</strong>
        <Badge tone={tone}>{label}</Badge>
      </div>
      <p><RichText text={pick(v.summary)} /></p>
      {group(lang === "zh" ? "可能适用的保障条款" : "Clauses that appear to cover it", v.covering_clauses)}
      {group(lang === "zh" ? "可能相关的除外责任" : "Possible exclusions", v.possible_exclusions)}
      {group(lang === "zh" ? "需满足的条件" : "Conditions to meet", v.conditions)}
      {group(lang === "zh" ? "金额条款" : "Financial terms", v.financial_terms)}
      {v.limit_notes?.map((n, i) => (
        <div key={i} className="alert warn">{pick(n)}</div>
      ))}
      {v.disclaimer && <div className="small muted">{pick(v.disclaimer)}</div>}
    </div>
  );
}

export function GapFindings({ findings, showCovered }: { findings: Finding[]; showCovered?: boolean }) {
  const { t, pick } = useLang();
  const list = showCovered ? findings : findings.filter((f) => f.status !== "covered");
  return (
    <div className="findings">
      {list.map((f) => (
        <div key={f.need} className="finding">
          <div className={`bar ${f.status}`} />
          <div>
            <div className="row" style={{ gap: 8 }}>
              <strong>{pick(f.label)}</strong>
              <StatusBadge status={f.status} label={t(f.status)} />
              <span className="small muted">{f.severity}</span>
            </div>
            <p style={{ margin: "4px 0" }}>{pick(f.explanation)}</p>
            <div className="small" style={{ color: "var(--ink-2)" }}>
              <b>{t("suggested")}:</b> {pick(f.suggested_action)}
            </div>
            <div className="row small muted" style={{ marginTop: 4, gap: 6 }}>
              <span>{t("triggered_by")}: {f.triggered_by}</span>
              {f.evidence.map((e) => (
                <CitationChip key={e.citation} citation={e.citation} />
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function ApprovalCard({ approval, onDone }: { approval: Approval; onDone?: (a: Approval) => void }) {
  const { t, pick, lang } = useLang();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [state, setState] = useState<Approval>(approval);
  const decide = async (kind: "approve" | "reject") => {
    if (!state.token) {
      toast(lang === "zh" ? "该卡片不含批准令牌，请重新生成。" : "This card has no approval token; ask the assistant to prepare it again.");
      return;
    }
    setBusy(true);
    try {
      const r = await api.post<{ approval: Approval }>(`/api/approvals/${state.id}/${kind}`, { token: state.token });
      setState({ ...r.approval, token: state.token });
      onDone?.(r.approval);
    } catch (e) {
      toast(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const result = state.result || {};
  return (
    <div className="block approval">
      <div className="row between">
        <h4 style={{ margin: 0 }}>{t("approval_required")}</h4>
        <StatusBadge status={state.status} />
      </div>
      <p style={{ marginTop: 8 }}><strong>{state.summary}</strong></p>
      <div className="small" style={{ marginBottom: 8 }}>
        <b>{t("consequences")}:</b>
        <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
          {state.consequences.map((c, i) => (
            <li key={i}>{pick(c)}</li>
          ))}
        </ul>
      </div>
      {state.action_type === "send_communication" && (
        <details style={{ marginBottom: 8 }}>
          <summary className="small">{lang === "zh" ? "查看将要发送的内容" : "Show the message that will be sent"}</summary>
          <blockquote style={{ whiteSpace: "pre-wrap", margin: "6px 0" }}>{String((state.payload as { body?: string }).body || "")}</blockquote>
        </details>
      )}
      {state.action_type === "submit_claim" && (
        <div className="small muted" style={{ marginBottom: 8 }}>
          {lang === "zh" ? "将发送：" : "Will share: "}{String((state.payload as { documents?: unknown[] }).documents?.length ?? 0)} {lang === "zh" ? "份文件 → " : "document(s) → "}{String(((state.payload as { recipient?: { organization?: string } }).recipient || {}).organization || "insurer")}
        </div>
      )}
      {state.status === "pending" ? (
        <div className="row">
          <button className="btn primary" disabled={busy} onClick={() => decide("approve")}>{t("approve")}</button>
          <button className="btn" disabled={busy} onClick={() => decide("reject")}>{t("reject")}</button>
          <span className="small muted">{t("nothing_sent")} {t("expires")} {fmtDateTime(state.expires_at, lang)}</span>
        </div>
      ) : (
        <div className={`alert ${state.status === "consumed" && result.status !== "failed" ? "ok" : state.status === "rejected" ? "" : "bad"}`}>
          {state.status === "consumed" && result.status !== "failed" && (lang === "zh" ? "已执行。" : "Done.")}{" "}
          {result.insurer_reference ? `${lang === "zh" ? "受理编号" : "Reference"}: ${String(result.insurer_reference)}${result.mock ? ` (${t("mock_ack")})` : ""}` : ""}
          {result.error ? `${lang === "zh" ? "失败：" : "Failed: "}${String(result.error)}` : ""}
          {state.status === "rejected" && (lang === "zh" ? "已拒绝，未发送任何内容。" : "Rejected. Nothing was sent.")}
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------- block router
export function MessageBlocks({ blocks, onApprovalDone }: { blocks: Record<string, unknown>[]; onApprovalDone?: () => void }) {
  const { t, lang } = useLang();
  const nodes: ReactNode[] = [];
  blocks.forEach((b, i) => {
    const type = b.type as string;
    if (type === "approval") nodes.push(<ApprovalCard key={i} approval={b.approval as Approval} onDone={onApprovalDone} />);
    else if (type === "checklist")
      nodes.push(
        <div className="block" key={i}>
          <div className="row between"><h4>{t("checklist")}</h4><Link className="small" to={`/claims/${b.claim_id as string}`}>{lang === "zh" ? "打开理赔页面" : "Open the claim"} <Icon name="arrowRight" size={13} /></Link></div>
          <Checklist items={b.items as ChecklistItem[]} compact />
          {b.completeness ? <div style={{ marginTop: 10 }}><CompletenessPanel c={b.completeness as Completeness} /></div> : null}
        </div>,
      );
    else if (type === "completeness")
      nodes.push(
        <div className="block" key={i}>
          <CompletenessPanel c={b.completeness as Completeness} />
        </div>,
      );
    else if (type === "coverage_view")
      nodes.push(
        <div className="block" key={i}>
          <CoverageViewPanel v={b.view as CoverageView} />
        </div>,
      );
    else if (type === "gap_findings")
      nodes.push(
        <div className="block" key={i}>
          <div className="row between"><h4>{t("gaps")}</h4><Link className="small" to="/gaps">{lang === "zh" ? "查看全部" : "See all"} <Icon name="arrowRight" size={13} /></Link></div>
          <GapFindings findings={b.findings as Finding[]} />
        </div>,
      );
    else if (type === "claim_card") {
      const c = b.claim as { id: string; reference: string; status: string; status_label: string; progress: number; policy_number: string };
      nodes.push(
        <div className="block" key={i}>
          <div className="row between">
            <div><h4 style={{ margin: 0 }}>{c.reference}</h4><span className="small muted">{c.policy_number}</span></div>
            <div className="row"><StatusBadge status={c.status} label={c.status_label} /><Link className="btn sm" to={`/claims/${c.id}`}>{t("view")}</Link></div>
          </div>
        </div>,
      );
    } else if (type === "communication_draft") {
      const c = b.communication as { id: string; subject: string; body: string; recipient?: string; status: string; claim_id?: string };
      nodes.push(<DraftCard key={i} comm={c} />);
    } else if (type === "insurer_request_draft") {
      nodes.push(
        <div className="block" key={i}>
          <h4>{lang === "zh" ? "补充材料请求草稿" : "Draft information request"}</h4>
          <ul style={{ margin: "0 0 8px", paddingLeft: 18 }}>{(b.requested_items as string[]).map((x) => <li key={x}>{x}</li>)}</ul>
          <blockquote style={{ whiteSpace: "pre-wrap" }}>{b.message as string}</blockquote>
          <div className="small muted">{lang === "zh" ? "未发送：请在右侧“要求补充材料”中粘贴并发送。" : "Not sent. Paste it into “Request information” in the workspace to send."}</div>
        </div>,
      );
    } else if (type === "warning") {
      const items = b.items as unknown[];
      nodes.push(
        <div className="alert warn" key={i} style={{ marginTop: 10 }}>
          {b.code === "injection_flags"
            ? (lang === "zh" ? "上传的文件中含有类似指令的文字，已被忽略且不会触发任何操作：" : "The uploaded document contains instruction like text. It was ignored and cannot trigger actions: ") + (items as { filename: string }[]).map((x) => x.filename).join(", ")
            : (lang === "zh" ? "以下引用无法在检索到的条款中核实：" : "Citations that could not be verified against the retrieved wording: ") + (items as string[]).join(", ")}
        </div>,
      );
    } else if (type === "citations") {
      const items = b.items as ClauseRef[];
      if (items.length)
        nodes.push(
          <div className="row small muted" key={i} style={{ marginTop: 10, gap: 6 }}>
            <span>{t("citations")}:</span>
            {items.map((c) => (
              <CitationChip key={c.citation} citation={c.citation} />
            ))}
          </div>,
        );
    }
  });
  return <>{nodes}</>;
}

export function DraftCard({ comm, onSent }: { comm: { id: string; subject: string; body: string; recipient?: string | null; status: string; claim_id?: string | null }; onSent?: () => void }) {
  const { t, lang } = useLang();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(comm.subject);
  const [body, setBody] = useState(comm.body);
  const [approval, setApproval] = useState<Approval | null>(null);
  const [status, setStatus] = useState(comm.status);
  const save = async () => {
    try {
      await api.patch(`/api/communications/${comm.id}`, { subject, body });
      setEditing(false);
      toast(lang === "zh" ? "草稿已保存" : "Draft saved");
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const send = async () => {
    try {
      const r = await api.post<{ approval: Approval }>(`/api/communications/${comm.id}/send`);
      setApproval(r.approval);
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  return (
    <div className="block">
      <div className="row between">
        <h4 style={{ margin: 0 }}>{lang === "zh" ? "信件草稿" : "Draft message"}</h4>
        <StatusBadge status={status === "draft" ? "draft" : status} label={status} />
      </div>
      <div className="small muted" style={{ margin: "6px 0" }}>{lang === "zh" ? "收件人" : "To"}: {comm.recipient || (lang === "zh" ? "保险公司" : "the insurer")}</div>
      {editing ? (
        <div className="stack">
          <input className="input" value={subject} onChange={(e) => setSubject(e.target.value)} />
          <textarea className="textarea" style={{ minHeight: 200 }} value={body} onChange={(e) => setBody(e.target.value)} />
          <div className="row"><button className="btn primary sm" onClick={save}>{t("save")}</button><button className="btn sm" onClick={() => setEditing(false)}>{t("cancel")}</button></div>
        </div>
      ) : (
        <>
          <div><strong>{subject}</strong></div>
          <blockquote style={{ whiteSpace: "pre-wrap" }}>{body}</blockquote>
        </>
      )}
      {status === "draft" && !approval && !editing && (
        <div className="row">
          <button className="btn sm" onClick={() => setEditing(true)}>{t("edit")}</button>
          <button className="btn primary sm" onClick={send}>{t("send")}…</button>
          <span className="small muted">{lang === "zh" ? "发送前需要你的批准" : "Sending needs your approval first"}</span>
        </div>
      )}
      {approval && (
        <ApprovalCard
          approval={approval}
          onDone={(a) => {
            if (a.status === "consumed") setStatus("sent");
            onSent?.();
          }}
        />
      )}
    </div>
  );
}

export function MoneyText({ v, currency }: { v: number | null | undefined; currency: string }) {
  const { lang } = useLang();
  return <>{money(v, currency, lang)}</>;
}
