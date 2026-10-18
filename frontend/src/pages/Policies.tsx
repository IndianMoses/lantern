import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CustomerShell } from "../components/Layout";
import { CitationProvider, type ClauseRef } from "../components/Markdown";
import { Badge, Empty, Field, Modal, Spinner, StatusBadge, useToast } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { fmtDate, money } from "../lib/format";
import { useLang } from "../lib/i18n";
import { PolicyCardView, type PolicyCard } from "./Overview";

interface Insurer { id: string; code: string; name: string; name_zh?: string }

export default function PoliciesPage() {
  const { t, lang } = useLang();
  const toast = useToast();
  const nav = useNavigate();
  const [policies, setPolicies] = useState<PolicyCard[] | null>(null);
  const [renewals, setRenewals] = useState<{ policy_id: string; message: string; days_left: number }[]>([]);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [meta, setMeta] = useState({ provider_name: "", product_name: "", insurance_type: "home", policy_number: "", start_date: "", end_date: "", premium_amount: "", excess_amount: "" });
  const [busy, setBusy] = useState(false);
  const load = () => api.get<{ policies: PolicyCard[]; renewals: typeof renewals }>("/api/policies").then((r) => { setPolicies(r.policies); setRenewals(r.renewals); });
  useEffect(() => { load(); }, []);
  const upload = async () => {
    if (!file) return;
    setBusy(true);
    const form = new FormData();
    form.append("file", file);
    Object.entries(meta).forEach(([k, v]) => v && form.append(k, v));
    try {
      const r = await api.upload<{ policy: PolicyCard; extraction: { clauses_found: number; matched_insurer: { name: string } | null; injection_flags: unknown[] } }>("/api/policies/upload", form);
      toast(`${r.extraction.clauses_found} ${lang === "zh" ? "条条款已提取" : "clauses extracted"}${r.extraction.injection_flags.length ? " ⚠" : ""}`);
      setUploadOpen(false);
      nav(`/policies/${r.policy.id}`);
    } catch (e) {
      toast(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <CustomerShell title={t("my_insurance")} subtitle={lang === "zh" ? "你的保单、条款与续保提醒。" : "Your policies, their clauses and renewal reminders."} actions={<button className="btn primary" onClick={() => setUploadOpen(true)}>{t("upload_policy")}</button>}>
      {renewals.length > 0 && (
        <div className="alert warn" style={{ marginBottom: 14 }}>
          {renewals.map((r) => <div key={r.policy_id}>⏰ <Link to={`/policies/${r.policy_id}`}>{r.message}</Link></div>)}
        </div>
      )}
      {!policies ? <Spinner /> : policies.length === 0 ? <Empty>{lang === "zh" ? "还没有保单。上传一份保单文件开始。" : "No policies yet. Upload a policy document to begin."}</Empty> : (
        <div className="grid cols-2">{policies.map((p) => <PolicyCardView key={p.id} p={p} />)}</div>
      )}
      {uploadOpen && (
        <Modal title={t("upload_policy")} onClose={() => setUploadOpen(false)}>
          <div className="stack">
            <div className="alert info small">{lang === "zh" ? "上传 PDF 或文本格式的保单条款。我们会提取条款并标记置信度；你可以修改后再激活。文件中的任何“指令”都只会被视为数据。" : "Upload the wording as PDF or text. Clauses are extracted with a confidence flag for you to correct before activating. Any instructions inside the file are treated as data only."}</div>
            <input type="file" accept=".pdf,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            <div className="grid cols-2">
              <Field label={t("provider")}><input className="input" value={meta.provider_name} onChange={(e) => setMeta({ ...meta, provider_name: e.target.value })} /></Field>
              <Field label={lang === "zh" ? "产品名称" : "Product name"}><input className="input" value={meta.product_name} onChange={(e) => setMeta({ ...meta, product_name: e.target.value })} /></Field>
              <Field label={lang === "zh" ? "险种" : "Insurance type"}>
                <select className="select" value={meta.insurance_type} onChange={(e) => setMeta({ ...meta, insurance_type: e.target.value })}>
                  {["home", "renters", "motor", "travel", "ebike", "gadget", "pet"].map((x) => <option key={x} value={x}>{x}</option>)}
                </select>
              </Field>
              <Field label={t("policy_number")}><input className="input" value={meta.policy_number} onChange={(e) => setMeta({ ...meta, policy_number: e.target.value })} /></Field>
              <Field label={lang === "zh" ? "起期" : "Start date"}><input className="input" type="date" value={meta.start_date} onChange={(e) => setMeta({ ...meta, start_date: e.target.value })} /></Field>
              <Field label={lang === "zh" ? "止期" : "End date"}><input className="input" type="date" value={meta.end_date} onChange={(e) => setMeta({ ...meta, end_date: e.target.value })} /></Field>
              <Field label={t("premium")}><input className="input" type="number" value={meta.premium_amount} onChange={(e) => setMeta({ ...meta, premium_amount: e.target.value })} /></Field>
              <Field label={t("excess")}><input className="input" type="number" value={meta.excess_amount} onChange={(e) => setMeta({ ...meta, excess_amount: e.target.value })} /></Field>
            </div>
            <div className="row"><button className="btn primary" disabled={!file || busy} onClick={upload}>{busy ? <Spinner /> : t("upload")}</button><button className="btn" onClick={() => setUploadOpen(false)}>{t("cancel")}</button></div>
          </div>
        </Modal>
      )}
    </CustomerShell>
  );
}

interface PolicyDetail {
  policy: PolicyCard;
  insurer: { name: string; claims_email: string; integration_mode: string } | null;
  clauses: (ClauseRef & { id: string; confidence: number; page: number | null })[];
  claims: { id: string; reference: string; status: string; status_label: string; incident_type: string }[];
  timeline: { at: string; type: string; summary: string; link?: string }[];
}

export function PolicyDetailPage() {
  const { id } = useParams();
  const { t, lang } = useLang();
  const toast = useToast();
  const [data, setData] = useState<PolicyDetail | null>(null);
  const [filter, setFilter] = useState<string>("all");
  const [insurers, setInsurers] = useState<Insurer[]>([]);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState(false);
  const [showTr, setShowTr] = useState<Record<string, boolean>>({});
  const load = () => api.get<PolicyDetail>(`/api/policies/${id}`).then(setData);
  useEffect(() => { load(); api.get<{ insurers: Insurer[] }>("/api/policies/insurers").then((r) => setInsurers(r.insurers)); }, [id]);
  if (!data) return <CustomerShell><Spinner /></CustomerShell>;
  const p = data.policy;
  const kinds = ["all", "coverage", "exclusion", "condition", "limit", "claims_procedure", "definition"];
  const shown = data.clauses.filter((c) => filter === "all" || c.kind === filter);
  const save = async (confirm = false) => {
    try {
      const body: Record<string, unknown> = { ...edit, confirm };
      if (body.premium_amount === "") delete body.premium_amount;
      if (body.excess_amount === "") delete body.excess_amount;
      await api.patch(`/api/policies/${id}`, body);
      setEditing(false);
      toast(confirm ? (lang === "zh" ? "保单已激活" : "Policy activated") : t("save"));
      load();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  return (
    <CustomerShell title={`${p.provider_name} · ${p.product_name}`} subtitle={`${p.insurance_type_label} · ${p.policy_number}`} actions={<Link className="btn primary" to={`/assistant?policy=${p.id}`}>{t("ask_about_policy")}</Link>}>
      <CitationProvider clauses={data.clauses}>
        {p.status === "pending_review" && (
          <div className="alert warn" style={{ marginBottom: 14 }}>
            <div className="row between">
              <span>{lang === "zh" ? "这是从你上传的文件中提取的，置信度较低。请核对并确认信息后激活。" : "This policy was extracted from your upload with limited confidence. Check the details, correct anything wrong, then confirm to activate it."}</span>
              <div className="row"><button className="btn sm" onClick={() => { setEdit({ provider_name: p.provider_name, product_name: p.product_name, policy_number: p.policy_number, start_date: p.start_date, end_date: p.end_date, premium_amount: String(p.premium_amount ?? ""), excess_amount: String(p.excess_amount ?? ""), insurer_org_id: p.insurer_org_id || "" }); setEditing(true); }}>{t("edit")}</button><button className="btn primary sm" onClick={() => save(true)}>{t("confirm_activate")}</button></div>
            </div>
          </div>
        )}
        <div className="grid cols-3" style={{ marginBottom: 16 }}>
          <div className="card" style={{ gridColumn: "span 2" }}>
            <div className="card-head"><h3>{lang === "zh" ? "保单信息" : "Policy details"}</h3><div className="row"><StatusBadge status={p.status} label={p.status === "active" ? (lang === "zh" ? "有效" : "Active") : p.status === "pending_review" ? t("pending_review") : t("expired")} />{p.status !== "pending_review" && <button className="btn sm" onClick={() => { setEdit({ provider_name: p.provider_name, product_name: p.product_name, policy_number: p.policy_number, start_date: p.start_date, end_date: p.end_date, premium_amount: String(p.premium_amount ?? ""), excess_amount: String(p.excess_amount ?? ""), insurer_org_id: p.insurer_org_id || "" }); setEditing(true); }}>{t("edit")}</button>}</div></div>
            <dl className="kv">
              <dt>{t("period")}</dt><dd>{fmtDate(p.start_date, lang)} – {fmtDate(p.end_date, lang)} <span className="muted">({p.days_to_end} {t("days")})</span></dd>
              <dt>{t("premium")}</dt><dd>{money(p.premium_amount, p.currency, lang)} {p.premium_frequency ? `/ ${p.premium_frequency}` : ""}</dd>
              <dt>{t("excess")}</dt><dd>{money(p.excess_amount, p.currency, lang)}</dd>
              <dt>{t("provider")}</dt><dd>{data.insurer ? `${data.insurer.name} · ${data.insurer.claims_email} · ${data.insurer.integration_mode === "mock" ? (lang === "zh" ? "模拟接口" : "mock integration") : data.insurer.integration_mode}` : <span className="muted">{lang === "zh" ? "未连接平台上的保险公司（理赔将使用辅助提交模式）" : "Not connected to an insurer on the platform (claims use assisted submission)"}</span>}</dd>
              <dt>{lang === "zh" ? "来源" : "Source"}</dt><dd>{p.source === "uploaded" ? (lang === "zh" ? "你上传的文件" : "Your upload") : lang === "zh" ? "演示数据" : "Demonstration data"}{p.extraction_confidence != null && p.extraction_confidence < 1 && <span className="muted"> · {lang === "zh" ? "提取置信度" : "extraction confidence"} {Math.round(p.extraction_confidence * 100)}%</span>}</dd>
            </dl>
            {(p.summary?.key_limits as string[] | undefined)?.length ? <div style={{ marginTop: 10 }}><b className="small">{t("key_limits")}:</b> <span className="small">{(p.summary.key_limits as string[]).join(" · ")}</span></div> : null}
            {(p.summary?.key_exclusions as string[] | undefined)?.length ? <div><b className="small">{t("key_exclusions")}:</b> <span className="small">{(p.summary.key_exclusions as string[]).join(" · ")}</span></div> : null}
          </div>
          <div className="card">
            <h3>{t("timeline")}</h3>
            <ul className="timeline">{data.timeline.map((e, i) => <li key={i}><div className="when">{fmtDate(e.at, lang)}</div>{e.link ? <Link to={e.link}>{e.summary}</Link> : e.summary}</li>)}</ul>
          </div>
        </div>
        <div className="card">
          <div className="card-head">
            <h3>{t("clauses")} <span className="muted small">({data.clauses.length})</span></h3>
            <div className="tabs" style={{ margin: 0, border: 0 }}>{kinds.map((k) => <button key={k} className={filter === k ? "active" : ""} onClick={() => setFilter(k)}>{k.replace("_", " ")}</button>)}</div>
          </div>
          <div className="stack">
            {shown.map((c) => {
              const tr = c.translations?.[lang];
              return (
                <div key={c.id} style={{ padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
                  <div className="row" style={{ gap: 8 }}>
                    <span className="chip" style={{ cursor: "default" }}>{c.citation}</span>
                    <strong>{c.title}</strong>
                    <Badge tone={c.kind === "exclusion" ? "bad" : c.kind === "coverage" ? "ok" : c.kind === "limit" ? "warn" : "info"} plain>{c.kind}</Badge>
                    {c.confidence < 1 && <Badge tone="warn" plain>{lang === "zh" ? "提取" : "extracted"} {Math.round(c.confidence * 100)}%</Badge>}
                    {c.amount != null && c.amount > 0 && <span className="small muted">{c.amount.toLocaleString()}</span>}
                    {tr && c.language !== lang && <button className="btn ghost sm" style={{ padding: 0 }} onClick={() => setShowTr({ ...showTr, [c.id]: !showTr[c.id] })}>{showTr[c.id] ? t("original") : t("translate")}</button>}
                  </div>
                  <div className="small muted">{c.section}</div>
                  <p style={{ margin: "4px 0 0", whiteSpace: "pre-wrap" }}>{showTr[c.id] && tr ? tr : c.text}</p>
                </div>
              );
            })}
          </div>
        </div>
        {data.claims.length > 0 && (
          <div className="card">
            <h3>{t("claims")}</h3>
            {data.claims.map((c) => <div key={c.id} className="row between" style={{ padding: "6px 0" }}><Link to={`/claims/${c.id}`} className="mono">{c.reference}</Link><span>{c.incident_type.replace(/_/g, " ")}</span><StatusBadge status={c.status} label={c.status_label} /></div>)}
          </div>
        )}
        {editing && (
          <Modal title={t("edit")} onClose={() => setEditing(false)}>
            <div className="grid cols-2">
              {(["provider_name", "product_name", "policy_number", "start_date", "end_date", "premium_amount", "excess_amount"] as const).map((k) => (
                <Field key={k} label={k.replace(/_/g, " ")}><input className="input" type={k.includes("date") ? "date" : k.includes("amount") ? "number" : "text"} value={edit[k] || ""} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} /></Field>
              ))}
              <Field label={lang === "zh" ? "平台上的保险公司" : "Insurer on the platform"}>
                <select className="select" value={edit.insurer_org_id || ""} onChange={(e) => setEdit({ ...edit, insurer_org_id: e.target.value })}>
                  <option value="">{lang === "zh" ? "未连接（辅助提交）" : "Not connected (assisted submission)"}</option>
                  {insurers.map((o) => <option key={o.id} value={o.id}>{lang === "zh" && o.name_zh ? o.name_zh : o.name}</option>)}
                </select>
              </Field>
            </div>
            <div className="row" style={{ marginTop: 14 }}><button className="btn primary" onClick={() => save(p.status === "pending_review")}>{p.status === "pending_review" ? t("confirm_activate") : t("save")}</button><button className="btn" onClick={() => setEditing(false)}>{t("cancel")}</button></div>
          </Modal>
        )}
      </CitationProvider>
    </CustomerShell>
  );
}
