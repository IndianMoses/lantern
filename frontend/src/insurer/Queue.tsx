import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Icon } from "../components/Icons";
import { InsurerShell } from "../components/Layout";
import { Badge, Empty, Spinner, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { fmtDate, money } from "../lib/format";
import { useLang } from "../lib/i18n";

export interface QueueRow { id: string; reference: string; customer: { id: string; name: string }; insurance_type_label: string; incident_type_label: string; incident_date: string | null; submitted_at: string | null; status: string; status_label: string; completeness: number | null; completeness_ready: boolean | null; assigned_handler: string | null; priority: string; updated_at: string; outstanding_action: string | null; estimated_loss: number | null; currency: string; age_days: number | null; sla_breach: boolean; insurer_reference: string | null }

export default function QueuePage() {
  const { lang } = useLang();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState<QueueRow[] | null>(null);
  const [total, setTotal] = useState(0);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [team, setTeam] = useState<{ id: string; name: string }[]>([]);
  const status = params.get("status") || "";
  const handler = params.get("handler") || "";
  const q = params.get("q") || "";
  const sort = params.get("sort") || "-updated_at";
  const page = Number(params.get("page") || 1);
  const set = (k: string, v: string) => { const n = new URLSearchParams(params); if (v) n.set(k, v); else n.delete(k); if (k !== "page") n.delete("page"); setParams(n); };
  useEffect(() => { api.get<{ team: typeof team }>("/api/insurer/team").then((r) => setTeam(r.team)); }, []);
  useEffect(() => {
    const qs = new URLSearchParams({ sort, page: String(page), page_size: "25" });
    if (status) qs.set("status", status);
    if (handler) qs.set("handler", handler);
    if (q) qs.set("q", q);
    api.get<{ claims: QueueRow[]; total: number; statuses: string[] }>(`/api/insurer/claims?${qs}`).then((r) => { setRows(r.claims); setTotal(r.total); setStatuses(r.statuses); });
  }, [status, handler, q, sort, page, lang]);
  const action = (a: string | null) => ({ acknowledge_and_assign: lang === "zh" ? "确认并分配" : "Acknowledge & assign", waiting_for_customer: lang === "zh" ? "等待客户" : "Waiting for customer", assess: lang === "zh" ? "评估" : "Assess", settle: lang === "zh" ? "结案赔付" : "Settle" } as Record<string, string>)[a || ""] || "—";
  const sortBtn = (k: string, label: string) => <button className="btn ghost sm" style={{ padding: 0, fontWeight: 600, color: sort.endsWith(k) ? "var(--accent-ink)" : undefined }} onClick={() => set("sort", sort === `-${k}` ? k : `-${k}`)}>{label} {sort === `-${k}` ? "↓" : sort === k ? "↑" : ""}</button>;
  return (
    <InsurerShell title={lang === "zh" ? "理赔队列" : "Claims queue"} subtitle={`${total} ${lang === "zh" ? "个案件" : "cases"}`}>
      <div className="row" style={{ marginBottom: 12 }}>
        <input className="input" style={{ maxWidth: 260 }} placeholder={lang === "zh" ? "搜索编号、描述、地点" : "Search reference, description, location"} defaultValue={q} onKeyDown={(e) => e.key === "Enter" && set("q", (e.target as HTMLInputElement).value)} />
        <select className="select" style={{ maxWidth: 240 }} value={status} onChange={(e) => set("status", e.target.value)}>
          <option value="">{lang === "zh" ? "全部状态" : "All statuses"}</option>
          <option value="submitted,acknowledged,under_review,additional_information_requested,reopened">{lang === "zh" ? "进行中" : "Open"}</option>
          {statuses.filter((s) => !["draft", "information_required", "ready_for_review", "awaiting_customer_approval", "submission_failed"].includes(s)).map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
        </select>
        <select className="select" style={{ maxWidth: 220 }} value={handler} onChange={(e) => set("handler", e.target.value)}>
          <option value="">{lang === "zh" ? "全部理赔员" : "All handlers"}</option><option value="me">{lang === "zh" ? "我的案件" : "Assigned to me"}</option><option value="unassigned">{lang === "zh" ? "未分配" : "Unassigned"}</option>
          {team.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </div>
      <div className="card" style={{ padding: 0, overflow: "auto" }}>
        {!rows ? <div style={{ padding: 20 }}><Spinner /></div> : rows.length === 0 ? <Empty>—</Empty> : (
          <table className="table">
            <thead><tr><th>{sortBtn("reference", "Ref")}</th><th>{lang === "zh" ? "客户" : "Customer"}</th><th>{lang === "zh" ? "险种 / 事件" : "Product / incident"}</th><th>{sortBtn("incident_date", lang === "zh" ? "事件日期" : "Incident")}</th><th>{sortBtn("submitted_at", lang === "zh" ? "提交" : "Submitted")}</th><th>{sortBtn("status", lang === "zh" ? "状态" : "Status")}</th><th>{lang === "zh" ? "完整度" : "Pack"}</th><th>{lang === "zh" ? "理赔员" : "Handler"}</th><th>{sortBtn("priority", lang === "zh" ? "优先级" : "Priority")}</th><th>{lang === "zh" ? "下一步" : "Next action"}</th><th>{lang === "zh" ? "预计损失" : "Est. loss"}</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => nav(`/insurer/claims/${r.id}`)}>
                  <td className="mono">{r.reference}{r.sla_breach && <div><Badge tone="bad" plain>SLA</Badge></div>}</td>
                  <td>{r.customer.name}</td>
                  <td>{r.insurance_type_label}<div className="small muted">{r.incident_type_label}</div></td>
                  <td>{fmtDate(r.incident_date, lang)}</td>
                  <td>{fmtDate(r.submitted_at, lang)}{r.age_days != null && <div className="small muted">{r.age_days}d</div>}</td>
                  <td><StatusBadge status={r.status} label={r.status_label} /></td>
                  <td>{r.completeness != null ? `${r.completeness}%` : "—"}</td>
                  <td>{r.assigned_handler || <span className="muted">—</span>}</td>
                  <td><Badge tone={r.priority === "high" ? "bad" : ""} plain>{r.priority}</Badge></td>
                  <td className="small">{action(r.outstanding_action)}</td>
                  <td>{money(r.estimated_loss, r.currency, lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {total > 25 && <div className="row" style={{ marginTop: 10 }}><button className="btn sm icon" aria-label={lang === "zh" ? "上一页" : "Previous page"} disabled={page <= 1} onClick={() => set("page", String(page - 1))}><Icon name="arrowLeft" size={14} /></button><span className="small">{page} / {Math.ceil(total / 25)}</span><button className="btn sm icon" aria-label={lang === "zh" ? "下一页" : "Next page"} disabled={page * 25 >= total} onClick={() => set("page", String(page + 1))}><Icon name="arrowRight" size={14} /></button></div>}
    </InsurerShell>
  );
}
