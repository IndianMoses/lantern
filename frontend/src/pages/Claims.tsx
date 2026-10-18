import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CustomerShell } from "../components/Layout";
import { Badge, Empty, Field, Spinner, StatusBadge, useToast } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { fmtDate, money } from "../lib/format";
import { useLang } from "../lib/i18n";
import type { PolicyCard } from "./Overview";

export interface ClaimSummary { id: string; reference: string; policy_id: string | null; insurance_type: string; incident_type: string; incident_date: string | null; status: string; status_label: string; status_source: string; status_updated_at: string; estimated_loss: number | null; currency: string; progress: number | null; insurer_reference: string | null; updated_at: string; description?: string }

export default function ClaimsPage() {
  const { t, lang } = useLang();
  const [claims, setClaims] = useState<ClaimSummary[] | null>(null);
  useEffect(() => { api.get<{ claims: ClaimSummary[] }>("/api/claims").then((r) => setClaims(r.claims)); }, []);
  return (
    <CustomerShell title={t("claims")} subtitle={lang === "zh" ? "从报告事件到结论的全部过程。" : "Everything from reporting an incident to the insurer's decision."} actions={<Link className="btn primary" to="/claims/new">{t("new_claim")}</Link>}>
      {!claims ? <Spinner /> : claims.length === 0 ? <Empty>{lang === "zh" ? "还没有理赔。" : "No claims yet."}</Empty> : (
        <div className="card">
          <table className="table">
            <thead><tr><th>Ref</th><th>{t("incident")}</th><th>{lang === "zh" ? "日期" : "Date"}</th><th>{t("status")}</th><th>{lang === "zh" ? "进度" : "Progress"}</th><th>{lang === "zh" ? "预计损失" : "Est. loss"}</th></tr></thead>
            <tbody>
              {claims.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => (window.location.href = `/claims/${c.id}`)}>
                  <td className="mono"><Link to={`/claims/${c.id}`}>{c.reference}</Link></td>
                  <td>{c.incident_type.replace(/_/g, " ")}<div className="small muted">{c.description?.slice(0, 70)}</div></td>
                  <td>{fmtDate(c.incident_date, lang)}</td>
                  <td><StatusBadge status={c.status} label={c.status_label} /><div className="small muted">{t("from")} {c.status_source} · {fmtDate(c.status_updated_at, lang)}</div></td>
                  <td>{c.progress != null ? `${c.progress}%` : "—"}</td>
                  <td>{money(c.estimated_loss, c.currency, lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CustomerShell>
  );
}

interface Question { key: string; type: string; en: string; zh: string; required: boolean }
interface IncidentType { code: string; en: string; zh: string }

export function NewClaimPage() {
  const { t, lang } = useLang();
  const toast = useToast();
  const nav = useNavigate();
  const [policies, setPolicies] = useState<PolicyCard[]>([]);
  const [policyId, setPolicyId] = useState<string>("");
  const [types, setTypes] = useState<IncidentType[]>([]);
  const [incident, setIncident] = useState<string>("");
  const [questions, setQuestions] = useState<Question[]>([]);
  const [answers, setAnswers] = useState<Record<string, unknown>>({ items_involved: [] });
  const [step, setStep] = useState(1);
  const [ranked, setRanked] = useState<{ policy_id: string; policy_name: string; policy_number: string; verdict: string; score: number }[]>([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<{ policies: PolicyCard[] }>("/api/policies").then((r) => setPolicies(r.policies.filter((p) => p.status === "active"))); }, []);
  const policy = policies.find((p) => p.id === policyId);
  useEffect(() => {
    const it = policy?.insurance_type || "home";
    api.get<{ incident_types: IncidentType[] }>(`/api/claims/incident-types?insurance_type=${policyId ? it : "home"}`).then((r) => {
      if (!policyId) {
        // union over all types when no policy is chosen yet
        Promise.all(["home", "renters", "motor", "travel", "ebike", "gadget", "pet"].map((x) => api.get<{ incident_types: IncidentType[] }>(`/api/claims/incident-types?insurance_type=${x}`))).then((all) => {
          const seen = new Map<string, IncidentType>();
          all.forEach((a) => a.incident_types.forEach((x) => seen.set(x.code, x)));
          setTypes([...seen.values()]);
        });
      } else setTypes(r.incident_types);
    });
  }, [policyId, policy?.insurance_type]);
  useEffect(() => {
    if (!incident) return;
    api.get<{ questions: Question[] }>(`/api/claims/questions?insurance_type=${policy?.insurance_type || "home"}&incident_type=${incident}`).then((r) => setQuestions(r.questions));
  }, [incident, policy?.insurance_type]);
  const describe = String(answers.description || "");
  const suggest = async () => {
    const r = await api.post<{ ranked: typeof ranked }>("/api/claims/rank-policies", { incident_type: incident, description: describe });
    setRanked(r.ranked);
    if (!policyId && r.ranked[0] && r.ranked[0].verdict !== "not_addressed") setPolicyId(r.ranked[0].policy_id);
    setStep(3);
  };
  const create = async () => {
    setBusy(true);
    try {
      const base: Record<string, unknown> = { policy_id: policyId || null, insurance_type: policy?.insurance_type, incident_type: incident };
      const known = new Set(["incident_date", "incident_time", "location", "description", "items_involved", "estimated_loss", "actions_taken", "safety_concern", "third_party_involved", "authorities_involved", "authority_reference"]);
      const extra: Record<string, unknown> = {};
      Object.entries(answers).forEach(([k, v]) => { if (known.has(k)) base[k] = v === "" ? null : v; else if (v !== "" && v !== undefined) extra[k] = v; });
      base.extra_answers = extra;
      base.loss_unknown = base.estimated_loss === null || base.estimated_loss === undefined;
      const r = await api.post<{ claim: { id: string } }>("/api/claims", base);
      nav(`/claims/${r.claim.id}`);
    } catch (e) {
      toast(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const set = (k: string, v: unknown) => setAnswers((a) => ({ ...a, [k]: v }));
  const items = (answers.items_involved as { label: string; value: string }[]) || [];
  return (
    <CustomerShell title={t("new_claim")} subtitle={lang === "zh" ? "描述发生的事情；我们会对照条款并生成材料清单。提交前不会发送任何内容。" : "Tell us what happened. We read the wording, build the checklist, and send nothing until you approve."}>
      <div className="card">
        <div className="tabs">{[1, 2, 3].map((s) => <button key={s} className={step === s ? "active" : ""} disabled>{s}. {s === 1 ? (lang === "zh" ? "事件类型" : "What happened") : s === 2 ? (lang === "zh" ? "详细信息" : "Details") : lang === "zh" ? "适用保单" : "Which policy"}</button>)}</div>
        {step === 1 && (
          <div className="stack">
            {answers.safety_concern ? null : <div className="alert info small">{lang === "zh" ? "如有人受伤或处于危险中，请先联系急救或报警。" : "If anyone is hurt or in danger, contact the emergency services first."}</div>}
            <Field label={lang === "zh" ? "涉及哪份保单？（不确定可留空）" : "Which policy? (leave blank if unsure)"}>
              <select className="select" value={policyId} onChange={(e) => setPolicyId(e.target.value)}>
                <option value="">{lang === "zh" ? "不确定 / 让系统建议" : "Not sure, suggest one"}</option>
                {policies.map((p) => <option key={p.id} value={p.id}>{p.provider_name} {p.product_name} ({p.policy_number})</option>)}
              </select>
            </Field>
            <div className="wizard-grid">{types.map((x) => <button key={x.code} className={incident === x.code ? "active" : ""} onClick={() => setIncident(x.code)}>{lang === "zh" ? x.zh : x.en}</button>)}</div>
            <div className="row"><button className="btn primary" disabled={!incident} onClick={() => setStep(2)}>{t("next")}</button></div>
          </div>
        )}
        {step === 2 && (
          <div className="stack">
            {questions.map((q) => (
              <Field key={q.key} label={(lang === "zh" ? q.zh : q.en) + (q.required ? " *" : "")}>
                {q.type === "textarea" ? <textarea className="textarea" value={String(answers[q.key] ?? "")} onChange={(e) => set(q.key, e.target.value)} />
                  : q.type === "boolean" ? <select className="select" value={answers[q.key] === undefined || answers[q.key] === "" ? "" : answers[q.key] ? "yes" : "no"} onChange={(e) => set(q.key, e.target.value === "" ? "" : e.target.value === "yes")}><option value="">—</option><option value="yes">{lang === "zh" ? "是" : "Yes"}</option><option value="no">{lang === "zh" ? "否" : "No"}</option></select>
                  : q.type === "items" ? (
                    <div className="stack" style={{ gap: 6 }}>
                      {items.map((it, i) => (
                        <div className="row" key={i}><input className="input" placeholder={lang === "zh" ? "物品" : "Item"} value={it.label} onChange={(e) => set("items_involved", items.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} /><input className="input" style={{ maxWidth: 140 }} type="number" placeholder={lang === "zh" ? "价值" : "Value"} value={it.value} onChange={(e) => set("items_involved", items.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} /><button className="btn sm" onClick={() => set("items_involved", items.filter((_, j) => j !== i))}>✕</button></div>
                      ))}
                      <button className="btn sm" style={{ alignSelf: "flex-start" }} onClick={() => set("items_involved", [...items, { label: "", value: "" }])}>+ {lang === "zh" ? "添加物品" : "Add item"}</button>
                    </div>
                  )
                  : <input className="input" type={q.type === "date" ? "date" : q.type === "time" ? "time" : q.type === "money" ? "number" : "text"} value={String(answers[q.key] ?? "")} onChange={(e) => set(q.key, q.type === "money" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)} />}
              </Field>
            ))}
            <div className="row"><button className="btn" onClick={() => setStep(1)}>{t("back")}</button><button className="btn primary" disabled={!describe || !answers.incident_date || !answers.location} onClick={suggest}>{t("next")}</button></div>
          </div>
        )}
        {step === 3 && (
          <div className="stack">
            <p className="muted">{lang === "zh" ? "根据你的描述，各保单的条款解读如下（不是结论）。选择要据以申请的保单。" : "Based on your description, this is how each policy's wording reads (not a decision). Pick the policy to claim against."}</p>
            {ranked.map((r) => (
              <label key={r.policy_id} className="row between" style={{ padding: "10px 12px", border: `1px solid ${policyId === r.policy_id ? "var(--accent)" : "var(--line)"}`, borderRadius: 10, cursor: "pointer" }}>
                <span className="row"><input type="radio" name="policy" checked={policyId === r.policy_id} onChange={() => setPolicyId(r.policy_id)} /> <b>{r.policy_name}</b> <span className="mono small">{r.policy_number}</span></span>
                <Badge tone={r.verdict.startsWith("appears_covered") ? "ok" : r.verdict === "not_addressed" ? "" : "warn"}>{r.verdict.replace(/_/g, " ")}</Badge>
              </label>
            ))}
            {ranked.length === 0 && <div className="alert warn">{lang === "zh" ? "没有有效保单。" : "No active policies to claim against."}</div>}
            <div className="row"><button className="btn" onClick={() => setStep(2)}>{t("back")}</button><button className="btn primary" disabled={busy} onClick={create}>{busy ? <Spinner /> : lang === "zh" ? "创建理赔草稿" : "Create claim draft"}</button></div>
          </div>
        )}
      </div>
    </CustomerShell>
  );
}
